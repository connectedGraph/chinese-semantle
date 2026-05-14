# 词向量数据目录

请将下载的腾讯 AI Lab 中文词向量文件解压后放在此目录，文件名命名为以下任意一个即可：

- `tencent_embedding.txt` （推荐）
- `tencent-ailab-embedding.txt`
- `light_Tencent_AILab_ChineseEmbedding.txt`

下载页面：https://ai.tencent.com/ailab/nlp/zh/embedding.html

## 推荐版本

| 版本 | 词数 | 维度 | 文件大小 | 加载内存 | 适合场景 |
|---|---|---|---|---|---|
| Tiny | 100 万 | 100 | ~1 GB | ~900 MB | **本地开发首选** |
| Small | 200 万 | 200 | ~4 GB | ~3.5 GB | 轻量生产 |
| Large | 800 万 | 200 | ~16 GB | ~13 GB | 完整生产 |

## 加速

首次加载会把 `.txt` 转换为 gensim 的 `.kv` 二进制格式（保存为 `embedding.kv`），
之后启动只需几秒钟。

如果你没有词向量文件，服务会自动以 **mock 模式** 启动，可用于联调前端，但相似度分数无意义。
