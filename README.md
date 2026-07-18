# 臺灣人文藝術資料庫（taiwan-arts-db）

教師備課用的臺灣人文藝術資料庫，與「認識臺灣」地理資料庫
（[taiwan-geo-db](https://elliot200852-lab.github.io/taiwan-geo-db/)）成對。
從具體的人出發——寫小說的、畫畫的、作曲的、演戲的——認識這片土地上的
文學與藝術，每頁附可直接帶進課堂的教學素材與說書切分提示。

站台：<https://elliot200852-lab.github.io/taiwan-arts-db/>

內容取材自 [Taiwan.md](https://taiwan.md/)（CC BY-SA 4.0）與各頁標示來源；
改作內容依同條款釋出，圖片授權逐張標示於圖說。

**架構一句話**：內容（HTML＋圖片）SSOT 全在 Google Drive，repo 只留腳本、
模板與 CSS，CI 部署時由 `scripts/pull_content.py` 拉取上架。

部署、Drive 夾、secret、驗證流程 → 見 [docs/DEPLOY.md](docs/DEPLOY.md)。
