# EAOS Kubernetes 部署清单

Phase 6 T7 产出：在 K8s 上运行 EAOS api + worker + migrate 所需的最小清单。

## 文件

| 文件 | 用途 |
|------|------|
| `namespace.yaml` | `eaos` 命名空间 |
| `configmap.yaml` | 非密配置（DB URL、Redis URL、OTel、模型默认值等） |
| `secret.yaml.example` | 密钥占位（JWT key、LLM API key）；复制为 `secret.yaml` 填真值 |
| `postgres.yaml` | 开发用 Postgres StatefulSet（生产请用托管 PG） |
| `api-deployment.yaml` | api Deployment (3 副本) + Service + Ingress |
| `worker-deployment.yaml` | evolution worker Deployment (1 副本) |
| `migrate-job.yaml` | alembic 迁移 Job（部署前手动运行） |

## 部署步骤

```bash
# 1. 构建并推送镜像（替换为你的镜像仓库）
docker build -t eaos-api:latest -f deploy/docker/Dockerfile.api .
docker build -t eaos-worker:latest -f deploy/docker/Dockerfile.worker .
# docker push <registry>/eaos-api:latest
# docker push <registry>/eaos-worker:latest

# 2. 创建命名空间与配置
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml

# 3. 创建密钥（先复制 example，填真值，再 apply）
cp deploy/k8s/secret.yaml.example deploy/k8s/secret.yaml
# 编辑 secret.yaml，填入 base64 编码的真实密钥
kubectl apply -f deploy/k8s/secret.yaml

# 4. 运行数据库迁移（一次性 Job）
kubectl apply -f deploy/k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/eaos-migrate -n eaos --timeout=120s

# 5. 部署 Postgres（仅开发；生产用托管 PG 时跳过本文件）
kubectl apply -f deploy/k8s/postgres.yaml

# 6. 部署 api + worker
kubectl apply -f deploy/k8s/api-deployment.yaml
kubectl apply -f deploy/k8s/worker-deployment.yaml

# 7. 验证
kubectl get pods -n eaos
kubectl port-forward svc/eaos-api 8000:80 -n eaos
curl http://localhost:8000/health   # 期望 {"status":"ok"}
```

## 关键决策

- **api 3 副本**：验证 Phase 4 PostgresSaver 多实例能力。横向扩展前需确认 worker 单实例推进策略。
- **worker 1 副本**：六步管线推进 + DPO 训练需串行，多副本会引发训练竞争。Phase 7 引入 Redis Streams / Celery 后再扩容。
- **Postgres**：本清单提供开发用 StatefulSet，生产环境务必使用托管 Postgres（RDS / Cloud SQL / AlloyDB），并在 `configmap.yaml` 中将 `EAOS_DB__URL` 指向外部实例后删除 `postgres.yaml`。
- **密钥**：只走 Secret，真值不入库。`secret.yaml.example` 仅占位，`secret.yaml` 必须加入 `.gitignore`。
- **migrate**：作为独立 Job 在部署前手动运行，避免多副本 api 启动时迁移竞争。

## Ingress

清单使用 `nginx` ingress class 与 `eaos.local` 测试域名。生产部署时请替换为真实域名并配置 TLS。
