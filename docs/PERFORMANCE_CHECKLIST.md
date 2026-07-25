# Performance and stability checklist

- 10,000項目の増分列挙中もイベントループが応答する
- 初回サムネイル要求が全件一括にならず、可視範囲と先読みを優先する
- 高速スクロール、並び替え、表示密度変更で旧generationを破棄する
- 500ページPDFの高速移動、ズーム、リサイズで古いレンダーを表示しない
- Viewerを20回作成・終了し、working setとhandle数が単調増加しない
- PDF／RAR／ZIPを交互に開き、PDFと外部processのhandleを解放する
- 100回のフォルダ切替後もscannerの後着結果を破棄する
- サムネイルキャッシュが設定容量を超えて増え続けない
- MetadataStore、PDFiumService、外部archive processが終了時に閉じる
- 子processと一時ファイルが残らず、終了時間が合理的である

`generate_stress_library.py`と`run_stability_smoke.py`は専用の出力・profileだけを使用し、結果を外部送信しません。速度の固定閾値より、応答停止、例外、handleリーク等の構造的失敗を重視します。
