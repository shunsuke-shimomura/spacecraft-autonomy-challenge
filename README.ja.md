# Spacecraft Autonomy Challenge: Template

[![License](https://img.shields.io/badge/license-MIT--0-blue.svg?style=flat)](LICENSE)

[English](README.md) | **日本語**

Spacecraft Autonomy Challenge の戦略を書くなら，ここから始めてください。
戦略はシミュレーション上の宇宙機を運用します。
テレメトリを読み取ってコマンドをスケジュールし，制約を守りながら，宇宙機の Δv を目標値まで増やします。

チャレンジ問題，シミュレータ，`autonomy-challenge` コマンドは，
[`spacecraft-autonomy-challenge-core`](https://github.com/conjikidow/spacecraft-autonomy-challenge-core) パッケージが提供します。
このパッケージは，`pyproject.toml` でリリースタグに固定されています。

## はじめに

### 必要なもの

- Python 3.12 以降
- [uv](https://docs.astral.sh/uv/)
- [Git](https://git-scm.com/)
- GitHub アカウント（このテンプレートから自分のリポジトリを作るため）

### リポジトリの作成

GitHub のこのリポジトリの上部にある **Use this template** ボタンで自分のリポジトリを作成し，クローンします。

```sh
git clone https://github.com/<your-account>/<your-repository>.git
cd <your-repository>
```

> 注：自分の戦略を残す必要がなければ，代わりにこのテンプレートを直接クローンしてもかまいません。

### ノートブックを開く

ノートブックをアプリとして実行します。

```sh
uv run marimo run app.py
```

ブラウザでアプリがグリッドレイアウトで開きます。
アプリでは，`submission/strategy.py` を編集し，公開シナリオで実行し，提出物を確認して，提出できます。

ノートブック自体を確認したり変更したりするには，エディタで開きます。

```sh
uv run marimo edit app.py
```

エディタでグリッドレイアウトを使うには，右下の **Toggle app view** ボタンか `Ctrl+.`（macOS では `Cmd+.`）で app view に切り替えてください。

## ノートブックでの作業

ノートブックには，宇宙機，ミッション，戦略 API が表示されます。
続いて，戦略に取り組む各ステップに対応するセクションがあります。

- **Submission**：`submission/` 以下のファイルをツリーから選んで編集します（最初は `submission/strategy.py`）。
  ツリーの右のボタンで新しいファイルを作成したり，選択中のファイルまたは空のフォルダを削除したりできます。
  `submission/strategy.py` は削除できません。
- **Try a scenario**：戦略を 1 つの公開シナリオで実行し，詳細を表示します。
- **Pack**：ダウンロード用の `submission.zip` を作成します。
- **Check on public scenarios**：`submission/` と `pyproject.toml` をまとめ，評価サーバーと同じ方法ですべての公開シナリオで評価します。
- **Evaluation server**：サーバーの設定を入力します。
- **Practice on hidden scenarios**：サーバーで練習評価を行います。
- **Submit the final version**：最終提出を行います。
- **Leaderboard**：順位表を表示します。

### ノートブックからの提出

イベントに評価サーバーがある場合は，運営者からその API の URL（`http://<host>:<port>/api/v1` など）と，必要ならトークンが伝えられます。
`http://<host>:<port>` のようにパスのない URL は `http://<host>:<port>/api/v1` として扱われます。

**Evaluation server** セクションにサーバーの設定を入力します。
URL とトークンは環境変数 `EVALUATION_SERVER_URL` と `EVALUATION_SERVER_TOKEN` から，
メールアドレスと名前は `git config` から，あらかじめ入力されています。
練習評価は **Practice on hidden scenarios** セクションから，最終提出は **Submit the final version** セクションから行えます。

サーバーに順位表がある場合は，**Leaderboard** セクションに表示されます。
最終提出を受け付けているかどうかと残り時間，公開後の最終順位も表示されます。

## 戦略の書き方

戦略は，`submission/strategy.py` の `MyStrategy` クラスとして書きます。
コードやファイルはすべて `submission/` の下に置いてください。
戦略ファイルのディレクトリがインポートパスの先頭に追加されるため，`submission/strategy.py` から `submission/` 内の他のモジュールをインポートできます。
コードと同じ場所に置いたデータファイルを読むときは，`Path(__file__).parent` を基準にパスを組み立ててください。

必要な Python パッケージは uv で追加します。
たとえば次のようにします。

```sh
uv add numpy
```

## 戦略 API

戦略は `commander.schedule(time_s, command)` でコマンドをスケジュールし，渡されるテレメトリを読みます。
`brief.nominal_initial_telemetry` は名目上の初期状態です。
実際の測定値はこれとずれることがあります。

| コマンド                  | 引数                     | 効果                                                     |
| ------------------------- | ------------------------ | -------------------------------------------------------- |
| `SetPropulsionMode(mode)` | `mode`: `PropulsionMode` | 推進モードを選択する                                     |
| `SetHeater(on)`           | `on`: `bool`             | 推進系のヒータをオンまたはオフにする                     |
| `SetMomentumDump(on)`     | `on`: `bool`             | モーメンタムダンプをオンまたはオフにする                 |
| `DeclareCompletion()`     | なし                     | 戦略が操作を完了したとみなすことを宣言し，実行を終了する |

`PropulsionMode` のメンバーは `OFF`，`THRUSTER_1`，`THRUSTER_2` です。
`Telemetry` のフィールドは `time_s`，`delta_v_mps`，`propulsion_temperature_c`，`battery_energy_wh`，
`wheel_momentum_nms`，`heater_on`，`propulsion_mode`，`momentum_dump_on` です。
コマンド，ブリーフ，宇宙機モデルの詳細は，下にリンクした core のドキュメントを参照してください。

## 練習評価の仕組み

- 結果には各ケースの標準出力と標準エラー出力の最後の数行が表示されるため，サーバー上での戦略のデバッグには `print` が使えます。
- 練習ケースには乱数で生成されるものが含まれ，提出のたびに引き直されます。
  そのため，同じ戦略でも提出ごとに得点が変わることがあります。
  固定ケースは毎回同じです。
- 順位表は固定ケースだけで順位を付けます。
  練習の順位表は，練習評価のすべてのケースではなく，サーバーの固定の練習ケースだけでの得点で順位を付けます。
  サーバーがこの得点を返す場合は，練習評価の結果にも **Fixed cases** として表示されます。
- 実行中の練習提出は参加者ごとに 1 件までです。前の提出が終わってから，もう一度提出してください。
- 既定では，練習提出の合計の制限時間は 600 秒（提出のすべてのケースの実行時間の合計。パッケージのインストール時間は含みません），
  各ケースの制限時間は 300 秒です。
  これらは評価サーバーの設定であり，主催者が変更することがあります。
  制限時間を超えたケースは `TIMEOUT` と表示されます。
  合計の制限時間を超えると，実行中だったケースと残りのケースが `TIMEOUT` と表示され，提出のステータスは完了のままです。
- 一時ファイルには `tempfile` を使い，固定のパスやポートを使わないでください。
- プランニングエンジンを使う場合，現行のリリースでは `up-lpg` に `setuptools<81` が必要です。
  Java ベースのエンジンには Java ランタイムが必要で，サーバーが既定のヒープ設定を適用することがあります。

## コマンドラインでの作業

コマンドラインは，ノートブックよりもコマンドラインを好む人のための代替手段です。

### 戦略の実行

`nominal` シナリオで戦略を実行します。

```sh
uv run autonomy-challenge run submission/strategy.py:MyStrategy
```

このコマンドは，シミュレーションがどのように終了したかと，宇宙機の真の状態から計算した評価指標を出力します。

利用可能なシナリオを一覧表示し，別のシナリオで実行します。

```sh
uv run autonomy-challenge scenarios
uv run autonomy-challenge run submission/strategy.py:MyStrategy --scenario thruster-degradation
```

`--json` を付けると，評価指標を JSON として出力します。

### 提出物の確認とパッケージ化

`submission/` と `pyproject.toml` をメモリ上で `submission.zip` にまとめ，評価サーバーと同じ方法ですべての公開シナリオで評価します。

```sh
uv run autonomy-challenge check
```

各シナリオでは戦略を新しい Python プロセスで起動し，`pyproject.toml` のパッケージのうち足りないものは，この確認のためだけにインストールします。
このコマンドは，得点，要約，ケースごとの表を出力します。
`uv run autonomy-challenge pack` は，評価せずに `submission.zip` をファイルに書き出します。

### 評価サーバーへの提出

運営者から伝えられた URL とトークンを，「ノートブックからの提出」で説明したとおりに使います。

```sh
export EVALUATION_SERVER_URL=<URL>
export EVALUATION_SERVER_TOKEN=<トークン>
uv run submit.py            # 非公開シナリオでの練習評価：結果を待って表示する
uv run submit.py --final    # 最終提出：受付票を表示する
```

Windows の場合や環境変数を使わない場合は，`--server <URL>` と `--token <トークン>` を指定してください。
練習評価の結果を待つのをやめても，評価はサーバー上で続きます。
あとから `uv run submit.py --status <ID>` で確認できます。
待っている間にサーバーが応答しなくなった場合（再起動など）は，最大 5 分間再試行します。
アップロード後にエラーが起きたときは，あとで結果を表示するための `--status` コマンドを出力します。
受け付けられた提出は，コマンドラインからでもノートブックからでも，プロジェクトのディレクトリにある `.submissions.log` に追記されます。
各行には ID，種別，時刻，サーバーのアドレスが入っているため，ID を調べ直せます。
このファイルは git の管理対象外です。
`<ID>` には，完全な ID のほか，順位表に表示される 8 文字のように，ID の先頭または末尾の一部を指定できます。
`submit.py` は，まず `.submissions.log` を，次にサーバー上の自分の提出を探します。
複数の提出に一致した場合は，それらを一覧表示します。
提出者は `git config user.email` と `git config user.name` で識別されます。
`--participant-id` と `--participant-name` で上書きでき，`uv run submit.py --help` ですべてのオプションを確認できます。

サーバーに順位表がある場合は，`uv run submit.py --board` で，最終提出を受け付けているかどうかと残り時間，公開後の最終順位とともに表示できます。
`--mine` を付けると，自分の練習提出だけを表示します。

## ドキュメント

API とチャレンジについては，core リポジトリのドキュメントを参照してください。
リンク先は，このテンプレートが使用している core のバージョンのドキュメントです。

- [戦略の書き方](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/strategy.ja.md)：ライフサイクル，`MissionBrief`，`Commander`
- [コマンドとタイムライン](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/timeline.ja.md)：コマンドの種類と順序規則
- [宇宙機モデル](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/spacecraft-model.ja.md)：状態，ダイナミクス，テレメトリ
- [シミュレーション](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/simulation.ja.md)：イベントの順序と終了条件
- [チャレンジ問題](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/challenge.ja.md)：チャレンジ問題とその公開シナリオ
- [評価](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/evaluation.ja.md)：シミュレーション後に出力される評価指標
- [コマンドラインツール](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/README.ja.md#コマンドラインツール)：`run`，`pack`，`check` のオプションと終了ステータス
- [提出アーカイブ](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/submission.ja.md)：`submission.zip` の内容
- [参加者のパッケージ](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/dependencies.ja.md)：`pyproject.toml` のパッケージの再現方法
- [得点](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/scoring.ja.md)：評価ケースと `Suite` の得点の計算方法

## ライセンス

このテンプレートは [MIT-0](LICENSE) ライセンスで提供されているため，著作権表示を残さなくても，これを土台に提出物を作れます。
あなたが追加したファイルはあなたのものであり，どのようにライセンスしても構いません。
