# 通常ホイール操作で途中の画像が飛ぶ件の事前比較

2026-09-22、相談側でのソース比較。対象は画像フォルダ。実装済みのcold wheel待機撤去とready wheel先読み継続を前提にする。ユーザーの症状は停止後の遅延ではなく、やや速い操作中の中間画像欠落。

## 参照と確認範囲

- ZipPlaFork固定revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`。
- ローカル`../ZipPlaViewer/source/ZipPla/ViewerForm.cs`のGit blobは`fb0fd278d2b5264e679348aa2b9224b86e8c7796`、`GenerarClasses.cs`は`ae22494d442e601c06945ac9da2741aeeaccf86b`。固定revisionのオブジェクトとの一致を確認済み。
- NivisViewerは現在の未コミット差分を含めて確認。私物画像や実アプリは開いていない。この比較自体は静的確認であり、ユーザー環境の原因を実測確定したものではない。

## 経路の違い

| 項目 | ZipPlaFork | NivisViewer |
| --- | --- | --- |
| 通常ページ送り | `NextPage`が現在の表示単位の準備状態を参照。未準備なら現在位置を返す経路があり、`movePageNatural`はそれ以上進めない。逆方向は単頁・見開きで条件が異なる | `ViewerPageNavigationController._move`はモデルを先に進める。表示の準備が間に合っていなくても要求位置は次へ進む |
| 読み込み中の方向・位置変更 | `SetWorksOrder`は未着手の順番を変更。実行中の仕事をそこでキャンセルせず、完了後に新しい順番から次を選ぶ | `_adopt_request`は次のcurrentが未キャッシュなら無関係な実行中jobをcancel。同じcurrentならadopt、currentが準備済みなら互換jobを継続可能 |
| 通常の描画 | `showCurrentPage → pbPaintInvalidate → Invalidate(false)`。同期描画を毎回保証しているわけではない | `commit_display_ready_frame → update()`。commitとpaintは別であり、paint前に次のframeへ置換され得る |
| 先読み | `SetBackgroundMode`で全体の順番を組み、基本1 worker。完了時に必要なら再開、容量不足で停止 | 共有runtimeの1実行枠。4順方向+1逆方向は優先部分で、その後も全体を読む。ready wheelの待機は直前の修正で撤去済み |
| キャッシュ | 表示用画像のサイズを主に管理し、低優先順位から破棄。元画像側にも保持・解放処理がある | 元画像と表示用画像の合算予算。通常active soft targetはhardの7/8、inactiveは1/2、OSメモリ圧力でさらに制限 |

主な参照位置: `ViewerForm.cs` NextPage 1903、movePageNatural 9279、showCurrentPage 6438、pbPaintInvalidate 6644、完了処理5371、SetNewResizedImage 5451、SetBackgroundMode 5558。`GenerarClasses.cs` SetWorksOrder 247、完了後の順番更新295付近。Nivisは`app/viewer_page_navigation.py`、`app/viewer_widget.py`のcommit/paint、`app/zip_raster_book_runtime.py`の`_adopt_request`/`_on_job_completed`/`_drive`。

## 今回の症状に対する判断

1. 現行wheel policyは入力間隔によらずIMMEDIATEを返す。従来の40ms定数は残るが、現在の通常wheel経路のSTAGE判定にはならない。閾値を緩めるだけでは対処にならない。
2. coldページは次の入力によって読み込みが中止され、要求位置だけが先へ進む可能性がある。ZipPlaの「未準備位置を越えて進みにくい」挙動との差は明確。ただし、これだけで今回の原因とは断定できない。
3. readyページも描画前に次のcommitが来れば見えない可能性がある。ZipPlaもInvalidateを使うため、APIの違いだけで優劣を断定しない。入力、commit、paintを分けた計測が必要。
4. 圧縮済みファイルの150KBとデコード・表示キャッシュ量は別。今回の欠落を容量不足と判断できる証拠はない。

## 方式比較と優先順位

- ZipPla方式: 読み込み済み範囲に移動を制限して順次表示を守りやすい。一方、読み込み待ちの入力が移動量に反映されない場合があり、そのまま移植するとNivisの操作感が変わる。
- 現行Nivis方式: 最後に指定した位置への追従を優先。中止が重なると途中の表示が減る可能性がある。
- Hybrid（第一候補）: 最新位置・世代管理を保持しつつ、準備済みframeの描画欠落をまず解消する。cold処理の継続・中止は別に実測し、有用な進行中処理を残す効果と最終位置の遅延を比較する。
- 新規方式: 入力を順番にすべて表示するキューは停止後の追いかけ再生を招く。ワーカー追加も描画欠落には効かず、今回の第一選択にしない。

まず全ready条件で独立した入力とpaintを記録し、次にcold条件でcancel回数・無駄になった処理・最終位置までの時間を比較する。全readyで欠落するならキャッシュ容量やdecode cancelをいじる前に描画経路を直す。coldの対策をreadyの修正と混ぜない。

## 作業状態

実装担当へ先に送ってしまった最新依頼は停止済み。停止前に`viewer_window.py`へready wheel後の即時paint呼び出しと`viewer_widget.py`の説明変更、未追跡`_wheel_trace_probe.py`が作られていた。これらは未検証・未受理として保持し、既に受理したcold wheel/先読み修正と区別する。追加実装依頼はこの比較後に内容を決める。

本資料は一次ソース比較の記録であり、コードのコピー・翻訳・移植は行っていない。既存のZipPlaFork AGPL-3.0-or-later通知は変更しない。

## 相談側で行った比較測定

同日、既存`.venv311` Python 3.11.9をサンドボックス外で起動し、`scripts/benchmark_folder_worker_comparison.py`を実行。環境修復や依存追加なし。750×1000の合成画像を各形式16枚、合計48枚だけ一時作成し、終了時に削除した。個人画像は不使用。

### 1並列・2並列

本番の`_ZipRasterUnitJob._render_unit()`を別々のページについて1/2並列で実行。1,2,2,1,1,2,2,1の順で各4回、中央値。OSキャッシュが効いた条件での読み込み・デコード・縮小の比較であり、共有runtimeの2ワーカー化、QPixmap転送、描画、HDD cold readを測ったものではない。

| 形式 | 平均ファイルサイズ | 1並列・16枚 | 2並列・16枚 |
| --- | ---: | ---: | ---: |
| JPEG | 218,607 bytes | 195.30ms | 116.18ms |
| PNG | 1,314,286 bytes | 247.35ms | 142.42ms |
| WebP | 234,922 bytes | 290.65ms | 155.06ms |

この局所条件では2並列に余地がある。表示要求の待ち時間やメモリ予約、sourceの並列アクセス契約を含む実際のscheduler設計は別途必要。単にthread pool上限を2へ変えても、runtimeがactive jobを1件に制限しているので2並列にはならない。

### 中止と進行中処理の完了

本番Folder runtimeで8回の独立した入力を20/40ms間隔で送り、読み込みに25msの人工遅延を加えた。実験側だけで、同一本・互換renderの開始済みjobを維持する条件へ切り替えて比較。先読みの追加実行は無効、request/結果のキャッシュ化/最新要求だけを公開する処理は本番のまま。各条件1回なので時間差の有意性を主張しない。

| 入力間隔 | 方式 | 中止回数 | 最後に残るキャッシュページ数 | 最終入力から完了 |
| --- | --- | ---: | ---: | ---: |
| 20ms | 現行中止 | 5 | 1 | 70.35ms |
| 20ms | 開始済みを完了 | 0 | 5 | 68.51ms |
| 40ms | 現行中止 | 7 | 1 | 45.12ms |
| 40ms | 開始済みを完了 | 0 | 8 | 51.46ms |

どの条件も画面への公開対象は最終ページのみだった。進行中処理を守ると成果をキャッシュへ残せるが、最新要求と一致しないframeを公開しない契約があるため、それだけで途中の画像欠落が直ると説明してはいけない。最後の位置は残りの1処理分だけ待つ可能性がある。

ユーザーは進行中の読み込みをZipPlaに合わせて完了させる変更を明示依頼。実装では未開始要求を最新へ置換し、開始済みで同一本・互換条件のものだけ完了させる。book switch/close/非互換renderや必要なメモリ圧力対応まで中止を無効化しない。ワーカー数は比較の依頼であり、この測定だけで本番2並列へ切り替えない。
