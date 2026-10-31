"""ΔV マヌーバのための縮小ホライズン型モデル予測制御 (MPC)。

テレメトリが届くたび(``decision_period_s`` ごと)に、コントローラは次を行う。

1. 新しいテレメトリから、真の機体状態とプラントパラメータの推定を更新する
   (``Estimator``)。ゆっくりしたズレはパラメータに吸収し、物理的に説明できない
   急な飛びはセンサバイアスとみなす。
2. ミッション終了時刻までをホライズンとする有限ホライズン最適制御問題を解き直す
   (``HorizonPlanner``)。温度・バッテリー・ホイールモーメンタムの制限に安全マージンを
   付けた制約のもとで、完了時刻を最小化する。
3. 計画の先頭 1 ステップだけを、秒未満の分解能のコマンドスケジュール
   (噴射開始・噴射終了・ヒーター・モーメンタムダンプ)として実行し、残りの計画は
   捨てて次のテレメトリで繰り返す。

プラントは 1 ステップ内の噴射時間に対して線形なので、内側の最適化は厳密に解ける。
各ステップで、1 ステップ先の制約が許す限り噴射し(温度上限に張り付かせる)、
モーメンタム管理方針に従ってスラスタを選び、最後の噴射で目標 ΔV にちょうど着地する。
方針の自由パラメータ(どれだけホイールモーメンタムを溜めてから反対スラスタや
ダンプを使うか)は、候補ごとにホライズン全体を転がして最も早く完了するものを選ぶ。

標準ライブラリのみを使うので、提出物に追加パッケージは不要。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from spacecraft_autonomy_challenge import (
    Commander,
    DeclareCompletion,
    MissionBrief,
    PropulsionMode,
    SetHeater,
    SetMomentumDump,
    SetPropulsionMode,
    Strategy,
    Telemetry,
)

THRUSTERS = (PropulsionMode.THRUSTER_1, PropulsionMode.THRUSTER_2)
SECONDS_PER_HOUR = 3600.0


# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MPCSettings:
    """コントローラのマージン・しきい値・方針候補。"""

    temperature_margin_c: float = 3.5
    """計画上の温度上限は、安全限界からこのマージンを引いた値。"""
    battery_margin_wh: float = 8.0
    """計画上のバッテリー下限は、安全限界にこのマージンを足した値。"""
    momentum_margin_nms: float = 1.0
    """計画上の |ホイールモーメンタム| の上限は、安全限界からこのマージンを引いた値。"""
    heater_margin_c: float = 2.0
    """温度が噴射可能最低温度にこの幅まで近づいたらヒーターを使う。"""
    momentum_policy_candidates: tuple[tuple[float, bool], ...] = (
        (0.0, False),
        (0.5, False),
        (1.5, False),
        (3.0, False),
        (2.0, True),
        (4.0, True),
    )
    """モーメンタム方針の候補 `(許容量 Nms, ダンプを使うか)`。許容量を超えたら、
    モーメンタムを減らす側のスラスタに切り替える(`False`)か、噴射を止めて
    ホイールモーメンタムをダンプする(`True`)。"""
    min_burn_s: float = 0.5
    """これより短い噴射は、目標に着地する噴射でなければ捨てる。"""
    landing_tolerance_mps: float = 1e-4
    """推定の不足量がこれ未満なら完了を宣言する。"""
    min_useful_acceleration_fraction: float = 0.05
    """公称加速度に対しこの割合を下回るスラスタは使わない。"""
    declare_without_verification: bool = True
    """直近のテレメトリがモデルと整合していれば、着地噴射の直後に完了宣言を
    スケジュールする。整合していなければ次のテレメトリで確認してから宣言する。"""
    # 推定器: これらのしきい値を超える残差はセンサバイアスの飛びとして扱う。
    plausible_acceleration_range: tuple[float, float] = (-0.05, 1.3)
    """物理的にありうるとみなす、公称比での見かけの加速度の範囲。"""
    temperature_jump_c: float = 2.5
    battery_jump_wh: float = 2.0
    momentum_jump_nms: float = 0.25
    adaptation_gain: float = 0.7
    """ありうる残差のうち、パラメータ推定に吸収する割合。"""
    min_burn_for_adaptation_s: float = 15.0
    consistent_jumps_to_accept: int = 3
    """「ありえない」ΔV 増分が互いに一致したままこの回数続いたら、
    実際の加速度変化として再分類する。"""
    verbose: bool = True


# --------------------------------------------------------------------------- #
# プラントモデルと状態
# --------------------------------------------------------------------------- #


@dataclass
class ThrusterModel:
    acceleration_mps2: float
    power_w: float
    heat_w: float
    momentum_rate_nms_per_s: float


@dataclass
class PlantModel:
    """コントローラが信じているパラメータ。公称値から始めてオンラインで適応する。"""

    thrusters: dict[PropulsionMode, ThrusterModel]
    dump_rate_nms_per_s: float
    dump_power_w: float
    dump_heat_w: float
    idle_net_power_w: float
    """発電量からバス負荷を引いた値。"""
    heater_power_w: float
    battery_capacity_wh: float
    heat_capacity_j_per_k: float
    conductance_w_per_k: float
    sink_temperature_c: float
    min_firing_temperature_c: float

    @classmethod
    def from_brief(cls, brief: MissionBrief) -> PlantModel:
        p = brief.nominal_parameters
        pairs = ((THRUSTERS[0], p.thruster_1), (THRUSTERS[1], p.thruster_2))
        thrusters = {
            mode: ThrusterModel(
                acceleration_mps2=t.acceleration_mps2,
                power_w=t.power_w,
                heat_w=t.heat_w,
                momentum_rate_nms_per_s=t.wheel_momentum_rate_nms_per_s,
            )
            for mode, t in pairs
        }
        return cls(
            thrusters=thrusters,
            dump_rate_nms_per_s=p.momentum_dump_rate_nms_per_s,
            dump_power_w=p.momentum_dump_power_w,
            dump_heat_w=p.momentum_dump_heat_w,
            idle_net_power_w=p.power_generation_w - p.bus_load_w,
            heater_power_w=p.heater_power_w,
            battery_capacity_wh=p.battery_capacity_wh,
            heat_capacity_j_per_k=p.heat_capacity_j_per_k,
            conductance_w_per_k=p.thermal_conductance_w_per_k,
            sink_temperature_c=p.sink_temperature_c,
            min_firing_temperature_c=p.min_firing_temperature_c,
        )

    def temperature_after(self, t0_c: float, heat_w: float, duration_s: float) -> float:
        """一定の熱入力 `heat_w` のもとで `duration_s` 経過後の温度。"""
        if duration_s <= 0:
            return t0_c
        c, g = self.heat_capacity_j_per_k, self.conductance_w_per_k
        if g <= 0:
            return t0_c + heat_w * duration_s / c
        t_eq = self.sink_temperature_c + heat_w / g
        return t_eq + (t0_c - t_eq) * math.exp(-g * duration_s / c)

    def time_to_reach(self, t0_c: float, heat_w: float, target_c: float) -> float:
        """温度が `target_c` まで上がるのにかかる時間。到達しないなら `inf`。"""
        if t0_c >= target_c:
            return 0.0
        c, g = self.heat_capacity_j_per_k, self.conductance_w_per_k
        if g <= 0:
            return math.inf if heat_w <= 0 else (target_c - t0_c) * c / heat_w
        t_eq = self.sink_temperature_c + heat_w / g
        if t_eq <= target_c:
            return math.inf
        return -(c / g) * math.log((target_c - t_eq) / (t0_c - t_eq))


@dataclass
class State:
    """推定した真の連続状態。"""

    time_s: float
    delta_v_mps: float
    temperature_c: float
    battery_wh: float
    momentum_nms: float


@dataclass(frozen=True)
class StepAction:
    """1 決定周期の間に機体が行うこと。

    ステップの先頭から `thruster` で `burn_s` 秒噴射する。残りの時間はアイドル、
    または `dump_on` ならモーメンタムのダンプに使う。
    """

    thruster: PropulsionMode = PropulsionMode.OFF
    burn_s: float = 0.0
    heater_on: bool = False
    dump_on: bool = False


def step_state(
    model: PlantModel, state: State, action: StepAction, dt_s: float
) -> State:
    """`action` のもとで `state` を 1 決定周期だけ進める。"""
    heater_w = model.heater_power_w if action.heater_on else 0.0
    dv, temp, mom = state.delta_v_mps, state.temperature_c, state.momentum_nms
    energy_wh = state.battery_wh
    burn = 0.0
    if (
        action.burn_s > 0
        and action.thruster in model.thrusters
        and not action.dump_on
        and temp >= model.min_firing_temperature_c
    ):
        burn = min(action.burn_s, dt_s)
        th = model.thrusters[action.thruster]
        dv += th.acceleration_mps2 * burn
        mom += th.momentum_rate_nms_per_s * burn
        temp = model.temperature_after(temp, heater_w + th.heat_w, burn)
        net_w = model.idle_net_power_w - heater_w - th.power_w
        energy_wh = min(
            energy_wh + net_w * burn / SECONDS_PER_HOUR, model.battery_capacity_wh
        )
    rest = dt_s - burn
    dump_heat_w = dump_power_w = 0.0
    if action.dump_on and rest > 0 and model.dump_rate_nms_per_s > 0 and mom != 0.0:
        # ダンプはモーメンタムが 0 になるまで全速で動き、そこで自動的に止まる。
        # 熱と電力は、残り時間のうち実際に動いた割合でスケールする。
        active = min(rest, abs(mom) / model.dump_rate_nms_per_s)
        fraction = active / rest
        mom = math.copysign(max(0.0, abs(mom) - model.dump_rate_nms_per_s * rest), mom)
        dump_heat_w = model.dump_heat_w * fraction
        dump_power_w = model.dump_power_w * fraction
    temp = model.temperature_after(temp, heater_w + dump_heat_w, rest)
    net_w = model.idle_net_power_w - heater_w - dump_power_w
    energy_wh = min(
        energy_wh + net_w * rest / SECONDS_PER_HOUR, model.battery_capacity_wh
    )
    return State(
        time_s=state.time_s + dt_s,
        delta_v_mps=dv,
        temperature_c=temp,
        battery_wh=energy_wh,
        momentum_nms=mom,
    )


# --------------------------------------------------------------------------- #
# 推定器: 状態・センサバイアス・パラメータ適応
# --------------------------------------------------------------------------- #


@dataclass
class Estimator:
    """テレメトリの裏にある真の状態を追跡し、プラントモデルを適応させる。

    各チャンネルについて、実行したアクションから次のテレメトリを予測する。
    1 ステップ内のどんな物理パラメータ変化でも説明できないほど大きな残差は
    センサバイアスの飛びとみなし、以降の読み値から取り除く。小さな残差は
    噴射したスラスタのパラメータに吸収する。
    """

    model: PlantModel
    nominal: PlantModel
    settings: MPCSettings
    dt_s: float
    bias: dict[str, float] = field(
        default_factory=lambda: {"dv": 0.0, "temp": 0.0, "batt": 0.0, "mom": 0.0}
    )
    state: State | None = None
    last_measured: Telemetry | None = None
    last_action: StepAction | None = None
    anomalies: list[str] = field(default_factory=list)
    steps_since_anomaly: int = 10**9
    _suspects: dict[PropulsionMode, list[tuple[float, float]]] = field(
        default_factory=dict
    )
    """スラスタごとに、そのスラスタの最後の「ありうる」読み値以降に「ありえない」と
    判定した読み値の (見かけの加速度, バイアスの飛び量)。"""

    def reset(self, telemetry: Telemetry, nominal_initial: Telemetry) -> State:
        """最初のテレメトリから開始する。

        同じ問題のシナリオはすべて初期状態を共有するので、最初の読み値が公称初期
        テレメトリと違っていれば、センサオフセットである可能性が高い。ΔV と
        ホイールモーメンタムはそのオフセット分を補正する。温度とバッテリーは、
        推定を誤ると安全限界を踏むおそれがあるため、安全側(より熱い、より少ない)
        になる方向にだけ補正する。
        """
        offsets = {
            "dv": telemetry.delta_v_mps - nominal_initial.delta_v_mps,
            "temp": telemetry.propulsion_temperature_c
            - nominal_initial.propulsion_temperature_c,
            "batt": telemetry.battery_energy_wh - nominal_initial.battery_energy_wh,
            "mom": telemetry.wheel_momentum_nms - nominal_initial.wheel_momentum_nms,
        }
        self.bias = {
            "dv": offsets["dv"],
            "temp": min(offsets["temp"], 0.0),
            "batt": max(offsets["batt"], 0.0),
            "mom": offsets["mom"],
        }
        self.last_measured = telemetry
        for name, value in offsets.items():
            if abs(value) > 1e-9:
                self._note(f"initial {name} reading off nominal by {value:+.4f}")
        self.state = State(
            time_s=telemetry.time_s,
            delta_v_mps=telemetry.delta_v_mps - self.bias["dv"],
            temperature_c=telemetry.propulsion_temperature_c - self.bias["temp"],
            battery_wh=telemetry.battery_energy_wh - self.bias["batt"],
            momentum_nms=telemetry.wheel_momentum_nms - self.bias["mom"],
        )
        return self.state

    def record_action(self, action: StepAction) -> None:
        self.last_action = action

    def update(self, telemetry: Telemetry) -> State:
        """新しいテレメトリを、直前に実行したアクションからの予測と突き合わせる。"""
        assert self.state is not None and self.last_measured is not None
        action = self.last_action or StepAction()
        predicted = step_state(
            self.model, self.state, action, telemetry.time_s - self.state.time_s
        )
        prev = self.last_measured
        self.steps_since_anomaly += 1
        burn = self._effective_burn(action)
        thruster = self.model.thrusters.get(action.thruster) if burn > 0 else None
        nominal = self.nominal.thrusters.get(action.thruster) if burn > 0 else None

        # -- ΔV ----------------------------------------------------------------- #
        increment = telemetry.delta_v_mps - prev.delta_v_mps
        if (
            thruster is not None
            and nominal is not None
            and nominal.acceleration_mps2 > 0
        ):
            # 噴射した: 増分から見かけの加速度を出し、ありうる範囲なら適応、
            # 範囲外ならセンサの飛びとみなす。
            implied = increment / burn
            low, high = self.settings.plausible_acceleration_range
            reference = max(nominal.acceleration_mps2, thruster.acceleration_mps2)
            lo_mps2, hi_mps2 = low * nominal.acceleration_mps2, high * reference
            if lo_mps2 <= implied <= hi_mps2:
                gain = self.settings.adaptation_gain
                old = thruster.acceleration_mps2
                thruster.acceleration_mps2 = max(0.0, old + gain * (implied - old))
                if abs(implied - old) > 0.02 * nominal.acceleration_mps2:
                    self._note(
                        f"accel {action.thruster.value} {old:.6f} -> {implied:.6f}"
                    )
                self._suspects.pop(action.thruster, None)
            else:
                jump = increment - thruster.acceleration_mps2 * burn
                self.bias["dv"] += jump
                self._note(
                    f"delta-v sensor jump {jump:+.4f} (bias now {self.bias['dv']:+.4f})"
                )
                self._suspects.setdefault(action.thruster, []).append((implied, jump))
                self._reconsider_suspects(action.thruster)
        elif abs(increment) > 1e-9:
            # 噴射していないのに ΔV が動いた: 確実にセンサの飛び。
            self.bias["dv"] += increment
            self._note(f"delta-v sensor jump {increment:+.4f} without firing")

        # -- 温度 ---------------------------------------------------------------- #
        residual = (
            telemetry.propulsion_temperature_c
            - self.bias["temp"]
            - predicted.temperature_c
        )
        if abs(residual) > self.settings.temperature_jump_c:
            self.bias["temp"] += residual
            self._note(f"temperature sensor jump {residual:+.2f} C")
        elif thruster is not None and burn >= self.settings.min_burn_for_adaptation_s:
            # 噴射中の残差はスラスタの発熱の見積もり誤差とみなす。
            heat_fix = residual * self.model.heat_capacity_j_per_k / burn
            new_heat = thruster.heat_w + self.settings.adaptation_gain * heat_fix
            thruster.heat_w = min(max(new_heat, 0.0), 3.0 * max(nominal.heat_w, 1.0))
        elif burn == 0 and not action.dump_on and abs(residual) > 0.05:
            # 冷却モデルが少しずれている: 見かけのシンク温度をずらす。
            self.model.sink_temperature_c += 0.5 * residual

        # -- バッテリー ---------------------------------------------------------- #
        residual = (
            telemetry.battery_energy_wh - self.bias["batt"] - predicted.battery_wh
        )
        near_cap = predicted.battery_wh >= self.model.battery_capacity_wh - 1e-6
        if abs(residual) > self.settings.battery_jump_wh:
            self.bias["batt"] += residual
            self._note(f"battery sensor jump {residual:+.2f} Wh")
        elif not near_cap:
            # 満充電付近はクランプされて情報がないので適応しない。
            if thruster is not None and burn >= self.settings.min_burn_for_adaptation_s:
                power_fix = -residual * SECONDS_PER_HOUR / burn
                new_power = thruster.power_w + self.settings.adaptation_gain * power_fix
                thruster.power_w = min(
                    max(new_power, 0.0), 3.0 * max(nominal.power_w, 1.0)
                )
            elif burn == 0 and not action.dump_on and abs(residual) > 0.01:
                self.model.idle_net_power_w += (
                    0.5 * residual * SECONDS_PER_HOUR / self.dt_s
                )

        # -- ホイールモーメンタム ------------------------------------------------ #
        residual = (
            telemetry.wheel_momentum_nms - self.bias["mom"] - predicted.momentum_nms
        )
        if abs(residual) > self.settings.momentum_jump_nms:
            self.bias["mom"] += residual
            self._note(f"momentum sensor jump {residual:+.3f} Nms")
        elif thruster is not None and burn >= self.settings.min_burn_for_adaptation_s:
            rate_fix = residual / burn
            thruster.momentum_rate_nms_per_s += self.settings.adaptation_gain * rate_fix

        # バイアスを除いた読み値を真の状態の推定とする。
        self.state = State(
            time_s=telemetry.time_s,
            delta_v_mps=telemetry.delta_v_mps - self.bias["dv"],
            temperature_c=telemetry.propulsion_temperature_c - self.bias["temp"],
            battery_wh=telemetry.battery_energy_wh - self.bias["batt"],
            momentum_nms=telemetry.wheel_momentum_nms - self.bias["mom"],
        )
        self.last_measured = telemetry
        return self.state

    def _effective_burn(self, action: StepAction) -> float:
        """モデル上、実際に推力が出ていたはずの噴射時間。"""
        assert self.state is not None
        if (
            action.burn_s <= 0
            or action.dump_on
            or action.thruster not in self.model.thrusters
        ):
            return 0.0
        if self.state.temperature_c < self.model.min_firing_temperature_c:
            return 0.0
        return min(action.burn_s, self.dt_s)

    def _reconsider_suspects(self, mode: PropulsionMode) -> None:
        """互いに一致した「飛び」が繰り返されるなら、それは実際の加速度変化。

        センサの飛びは一度しか起きない。モデルより強いスラスタは、噴射のたびに
        同じ「ありえない」見かけの加速度を出し続ける。一致した読み値が十分続いたら
        加速度の推定を更新し、それらの読み値に割り当てていたバイアスを返す。
        """
        suspects = self._suspects.get(mode, [])
        n = self.settings.consistent_jumps_to_accept
        if len(suspects) < n:
            return
        recent = [a for a, _ in suspects[-n:]]
        mean = sum(recent) / n
        if mean <= 0 or any(abs(a - mean) > 0.1 * abs(mean) for a in recent):
            return
        self.model.thrusters[mode].acceleration_mps2 = mean
        refund = sum(jump for _, jump in suspects)
        self.bias["dv"] -= refund
        del self._suspects[mode]
        self._note(
            f"reclassified as acceleration change of {mode.value}: {mean:.6f} "
            f"(refunded bias {refund:+.4f})"
        )

    def _note(self, text: str) -> None:
        self.anomalies.append(text)
        self.steps_since_anomaly = 0
        if self.settings.verbose:
            t = self.last_measured.time_s if self.last_measured else float("nan")
            print(f"[t={t:7.0f}] estimator: {text}")


# --------------------------------------------------------------------------- #
# ホライズンプランナ(MPC の最適化部分)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plan:
    first: StepAction
    completion_s: float | None
    """完了を宣言できると予測される時刻。完了しないなら None。"""
    final_delta_v_mps: float
    policy_momentum_nms: float
    policy_use_dump: bool
    steps: int
    landing_burn: bool
    """先頭ステップの噴射で目標 ΔV に到達するかどうか。"""


class HorizonPlanner:
    """現在の推定からミッション終了までの縮小ホライズン問題を解く。"""

    def __init__(self, brief: MissionBrief, settings: MPCSettings) -> None:
        self.brief = brief
        self.settings = settings
        self.dt_s = brief.decision_period_s
        limits = brief.limits
        self.ceiling_c = (
            limits.max_propulsion_temperature_c - settings.temperature_margin_c
        )
        self.floor_wh = limits.min_battery_energy_wh + settings.battery_margin_wh
        self.momentum_bound = (
            limits.max_abs_wheel_momentum_nms - settings.momentum_margin_nms
        )

    def plan(self, model: PlantModel, nominal: PlantModel, state: State) -> Plan:
        # 方針候補ごとにホライズン全体を転がし、最も早く完了するものを選ぶ。
        # コストが同じなら先に並んだ候補(モーメンタム許容量が小さいもの)を優先。
        best: Plan | None = None
        for h, use_dump in self.settings.momentum_policy_candidates:
            candidate = self._rollout(model, nominal, state, h, use_dump)
            if best is None or _plan_cost(candidate) < _plan_cost(best) - 1e-6:
                best = candidate
        assert best is not None
        return best

    # -- 1 ステップ分の決定則 ------------------------------------------------ #

    def step_action(
        self,
        model: PlantModel,
        nominal: PlantModel,
        state: State,
        h_tolerance: float,
        use_dump: bool,
    ) -> StepAction:
        dt = self.dt_s
        target = self.brief.target_delta_v_mps
        remaining_dv = target - state.delta_v_mps
        heater_on = (
            state.temperature_c
            < model.min_firing_temperature_c + self.settings.heater_margin_c
        )
        heater_w = model.heater_power_w if heater_on else 0.0
        if remaining_dv <= 0:
            return StepAction(heater_on=heater_on)

        # 使えるスラスタ(加速度が公称の一定割合以上あるもの)。
        usable = []
        for mode, th in model.thrusters.items():
            floor = (
                self.settings.min_useful_acceleration_fraction
                * nominal.thrusters[mode].acceleration_mps2
            )
            if th.acceleration_mps2 > max(floor, 1e-12):
                usable.append((mode, th))
        mom = state.momentum_nms

        def reduces(th: ThrusterModel) -> bool:
            # このスラスタが現在のモーメンタムを 0 に向けて減らすか。
            return th.momentum_rate_nms_per_s * mom < 0 or (
                mom == 0 and th.momentum_rate_nms_per_s <= 0
            )

        can_dump = model.dump_rate_nms_per_s > 0
        choice: tuple[PropulsionMode, ThrusterModel] | None = None
        if usable:
            if abs(mom) > h_tolerance and not use_dump:
                # 許容量を超えたので、モーメンタムを減らす側で最も強いスラスタ。
                reducing = [(m, th) for m, th in usable if reduces(th)]
                if reducing:
                    choice = max(reducing, key=lambda item: item[1].acceleration_mps2)
            if choice is None:
                # 最も強いスラスタ。同じ強さなら減らす側を優先(自然に交互になる)。
                choice = max(
                    usable,
                    key=lambda item: (item[1].acceleration_mps2, reduces(item[1])),
                )
        if use_dump:
            # 最強スラスタで噴射し、溜まったモーメンタムはダンプで捨てる方針。
            if can_dump and abs(mom) > max(h_tolerance, 0.05):
                return StepAction(heater_on=heater_on, dump_on=True)
        else:
            # どのスラスタでもモーメンタムを戻せない時だけダンプする。
            need_dump = abs(mom) > max(h_tolerance, 0.25) and (
                choice is None or not reduces(choice[1])
            )
            if need_dump and can_dump and abs(mom) > 0.5 * self.momentum_bound:
                return StepAction(heater_on=heater_on, dump_on=True)
        if choice is None:
            return StepAction(heater_on=heater_on)
        mode, th = choice

        if state.temperature_c < model.min_firing_temperature_c:
            # 冷えすぎて噴射できない: ヒーターで温める。
            return StepAction(heater_on=True)

        # 制約が許す最大噴射時間。温度は噴射終了時(ピーク)で評価する。
        burn = dt
        burn = min(
            burn,
            model.time_to_reach(
                state.temperature_c, heater_w + th.heat_w, self.ceiling_c
            ),
        )
        drain_w = th.power_w + heater_w - model.idle_net_power_w
        if drain_w > 0:
            burn = min(
                burn, (state.battery_wh - self.floor_wh) * SECONDS_PER_HOUR / drain_w
            )
        rate = th.momentum_rate_nms_per_s
        if rate != 0:
            # 噴射後のモーメンタムが、噴射で増える側の上限内に収まること。
            room = self.momentum_bound - mom * math.copysign(1.0, rate)
            burn = min(burn, max(0.0, room / abs(rate)))
        # 目標 ΔV にちょうど着地する噴射時間を超えない。
        landing = remaining_dv / th.acceleration_mps2
        burn = max(0.0, min(burn, landing))
        if burn < self.settings.min_burn_s and burn < landing:
            burn = 0.0
        if burn <= 0:
            return StepAction(heater_on=heater_on)
        return StepAction(thruster=mode, burn_s=burn, heater_on=heater_on)

    # -- ホライズン全体のロールアウト ---------------------------------------- #

    def _rollout(
        self,
        model: PlantModel,
        nominal: PlantModel,
        state: State,
        h_tolerance: float,
        use_dump: bool,
    ) -> Plan:
        target = self.brief.target_delta_v_mps
        time_limit = self.brief.time_limit_s
        dt = self.dt_s
        first: StepAction | None = None
        completion: float | None = None
        current = state
        steps = 0
        landing_first = False
        while current.time_s + dt <= time_limit + 1e-9:
            action = self.step_action(model, nominal, current, h_tolerance, use_dump)
            if first is None:
                first = action
            nxt = step_state(model, current, action, dt)
            steps += 1
            if nxt.delta_v_mps >= target - self.settings.landing_tolerance_mps:
                if steps == 1:
                    landing_first = True
                # 完了宣言は噴射直後、または次の決定時刻。
                completion = current.time_s + (
                    action.burn_s if self.settings.declare_without_verification else dt
                )
                current = nxt
                break
            current = nxt
        return Plan(
            first=first or StepAction(),
            completion_s=completion,
            final_delta_v_mps=current.delta_v_mps,
            policy_momentum_nms=h_tolerance,
            policy_use_dump=use_dump,
            steps=steps,
            landing_burn=landing_first,
        )


def _plan_cost(plan: Plan) -> float:
    """計画のコスト。完了時刻、完了しないなら大きな値から到達 ΔV を引いたもの。"""
    if plan.completion_s is not None:
        return plan.completion_s
    return 1e9 - plan.final_delta_v_mps * 1e6


# --------------------------------------------------------------------------- #
# 戦略
# --------------------------------------------------------------------------- #


class MPCStrategy(Strategy):
    """`HorizonPlanner` と `Estimator` で動く戦略。"""

    settings: MPCSettings = MPCSettings()

    def initialize(
        self, brief: MissionBrief, telemetry: Telemetry, commander: Commander
    ) -> None:
        self.brief = brief
        self.nominal = PlantModel.from_brief(brief)
        self.model = PlantModel.from_brief(brief)
        self.planner = HorizonPlanner(brief, self.settings)
        self.estimator = Estimator(
            self.model, self.nominal, self.settings, brief.decision_period_s
        )
        self.declared = False
        self.last_telemetry = telemetry
        state = self.estimator.reset(telemetry, brief.nominal_initial_telemetry)
        self._decide(state, telemetry, commander)

    def update(self, telemetry: Telemetry, commander: Commander) -> None:
        if self.declared:
            return
        state = self.estimator.update(telemetry)
        self.last_telemetry = telemetry
        self._decide(state, telemetry, commander)

    # -- 決定サイクル ------------------------------------------------------- #

    def _decide(self, state: State, telemetry: Telemetry, commander: Commander) -> None:
        now = commander.now_s
        dt = self.brief.decision_period_s
        target = self.brief.target_delta_v_mps
        # 前回の計画の残りをすべて取り消し、新しい計画で置き換える。
        commander.cancel_from(now)

        if state.delta_v_mps >= target - self.settings.landing_tolerance_mps:
            self._finish(commander, telemetry, "target reached")
            return
        if now + 2 * dt > self.brief.time_limit_s:
            # 時間切れ直前: 未宣言のまま終わるより宣言した方がスコアが良い。
            self._finish(commander, telemetry, "time limit imminent")
            return

        plan = self.planner.plan(self.model, self.nominal, state)
        action = plan.first
        self.estimator.record_action(action)

        # ヒーターとダンプ。
        if action.heater_on != telemetry.heater_on:
            commander.execute(SetHeater(on=action.heater_on))
        if action.dump_on:
            if telemetry.propulsion_mode is not PropulsionMode.OFF:
                commander.execute(SetPropulsionMode(PropulsionMode.OFF))
            if not telemetry.momentum_dump_on:
                commander.execute(SetMomentumDump(on=True))
        else:
            if telemetry.momentum_dump_on:
                commander.execute(SetMomentumDump(on=False))
            if action.burn_s > 0:
                # 今すぐ噴射開始、burn_s 秒後(秒未満の分解能)に停止。
                commander.execute(SetPropulsionMode(action.thruster))
                commander.schedule(
                    now + action.burn_s, SetPropulsionMode(PropulsionMode.OFF)
                )
                if (
                    plan.landing_burn
                    and self.settings.declare_without_verification
                    and self.estimator.steps_since_anomaly >= 2
                ):
                    # 着地噴射で、直近に異常がなければ噴射終了と同時に完了宣言。
                    commander.schedule(now + action.burn_s, DeclareCompletion())
                    self.declared = True
                    self._log(
                        f"landing burn {action.burn_s:.3f} s on "
                        f"{action.thruster.value}; "
                        f"completion scheduled at {now + action.burn_s:.3f} s"
                    )
            elif telemetry.propulsion_mode is not PropulsionMode.OFF:
                commander.execute(SetPropulsionMode(PropulsionMode.OFF))

        if self.settings.verbose and (int(now) % 600 == 0 or plan.landing_burn):
            eta = (
                f"{plan.completion_s:.0f}" if plan.completion_s is not None else "none"
            )
            self._log(
                f"dv={state.delta_v_mps:7.4f} T={state.temperature_c:6.2f} "
                f"E={state.battery_wh:6.2f} H={state.momentum_nms:+6.3f} | "
                f"{action.thruster.value} {action.burn_s:5.1f}s "
                f"heater={int(action.heater_on)} dump={int(action.dump_on)} | "
                f"h={plan.policy_momentum_nms}{'D' if plan.policy_use_dump else ''} "
                f"eta={eta}"
            )

    def _finish(self, commander: Commander, telemetry: Telemetry, reason: str) -> None:
        if telemetry.propulsion_mode is not PropulsionMode.OFF:
            commander.execute(SetPropulsionMode(PropulsionMode.OFF))
        commander.execute(DeclareCompletion())
        self.declared = True
        state = self.estimator.state
        dv = state.delta_v_mps if state else float("nan")
        self._log(f"declare completion ({reason}); estimated true dv={dv:.5f}")

    def _log(self, text: str) -> None:
        if self.settings.verbose:
            print(f"[t={self.last_telemetry.time_s:7.0f}] mpc: {text}")
