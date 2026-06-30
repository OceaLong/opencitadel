# 配置来源治理

## 唯一权威

| 类型 | 来源 | 示例 |
|------|------|------|
| 行为配置 | `AppConfig`（DB + config.yaml 种子） | `model_resilience`, `feature_flags`, `worker` |
| 密钥/连接 | `Settings` 环境变量 | `EMBEDDING_API_KEY`, Postgres/Redis |

## 生产部署

- **必须** `USE_DB_APP_CONFIG=true`（Helm `env` 已配置）
- `config.yaml` / Helm `appConfig` 为初始默认值；migrate job 在表空时种子写入 DB

## 禁止

- 不为行为开关新增平行环境变量（紧急止血除外：回滚镜像/配置）

## 同步清单

修改 `AppConfig` 字段时需同步：`app_config.py` schema、`config.yaml`、Helm `appConfig`、相关文档。
