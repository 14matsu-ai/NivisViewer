# Roadmap

1. Sprint 0：基盤整理と回帰テスト（完了）
2. Sprint 1：ApplicationController導入と責務分離（完了）
3. Sprint 2：ViewerWindowの独立（完了）
4. Sprint 3：BrowserWindowとフォルダ／サムネイル表示（完了）
5. Sprint 4：設定画面、見開き密着表示、永続サムネイルキャッシュ（完了）
6. Sprint 5：マウスジェスチャー、XButton書庫移動（完了）
7. Sprint 6：メタデータDB、ブックマーク、閲覧履歴（完了）
8. Sprint 7：BrowserWindowの基本ナビゲーション強化（完了）
9. Sprint 8：表示密度設定と並び替え（完了）
10. Sprint 9：大量項目の非同期・増分列挙（完了）
    - 可視範囲優先のサムネイル要求
    - 先読み範囲
    - 高速スクロール中の生成抑制
11. Sprint 10：安全なファイル操作（完了）
    - 名前変更、コピー、切り取り、貼り付け、指定先コピー／移動
    - Windowsごみ箱、新規フォルダ、複数選択
    - 直列非同期worker、進捗、キャンセル、部分失敗
    - MetadataStore、BrowserNavigationHistory、Viewer使用中確認
12. Sprint 11：RAR／7z／CBR／CB7対応（完了）
    - Windows関連付けからのWinRAR／7-Zip自動検出と明示指定
    - 拡張子ごとのbackend選択、認識済みCLIへの安全な変換
    - WinRAR公式コンソールCLIとSevenZipBackendの共存
    - 非同期の書庫一覧取得と単一ページstdout抽出
    - Browser、サムネイル、Viewer、前後の本、履歴への統合
    - 通常RARと`.part1.rar`を初期範囲とし、7z分割は正式対応外
13. Sprint 12：PDF対応
14. Sprint 13：レート／タグ編集UIと検索
    - 基本ナビゲーションとファイル操作の安定後に着手
    - ZipPla `{zpi$...}` の明示的な読み取り互換
    - タグ・レート検索と絞り込み
    - サムネイル上のレート／タグ表示
15. Sprint 14：ポータブルビルドと公開準備
