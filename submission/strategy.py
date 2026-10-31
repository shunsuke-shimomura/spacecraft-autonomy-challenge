"""Spacecraft Autonomy Challenge の戦略。

`MyStrategy` は `mpc.py` で定義した縮小ホライズン MPC コントローラそのもの。
戦略ファイルのあるディレクトリがインポートパスの先頭に置かれるので、
隣の `mpc.py` をそのままインポートできる。
"""

from mpc import MPCSettings, MPCStrategy


class MyStrategy(MPCStrategy):
    """テレメトリごとに残りミッション全体を再計画し、先頭 1 ステップを
    秒未満の分解能のコマンドスケジュールとして実行する。"""

    settings = MPCSettings()
