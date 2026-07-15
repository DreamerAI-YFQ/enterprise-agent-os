"""Seed the knowledge base with documents matching the RAG evaluation dataset.

Creates KB-PRD-001..010, KB-CUS-001..005, KB-ORD-001..005, KB-POL-001..003
via POST /api/knowledge/documents (admin auth required).
"""
import asyncio
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "benchmarks" / "competition"))

from runners.run_eval import API_BASE, ADMIN_EMAIL, ADMIN_PASSWORD, login

# Default tenant ID (acme)
TENANT_ID = "00000000-0000-0000-0000-000000000001"

DOCUMENTS = [
    # -- Product documents (KB-PRD-001..010) --
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-001",
        "title": "KB-PRD-001 智能路由器 RT-3800",
        "content": """# PRD-001 智能路由器 RT-3800

## 基本信息
- 产品编号: PRD-001
- 产品名称: 智能路由器 RT-3800
- 产品类别: 网络设备/路由器
- 单价: 2999元
- 供应商: 智能网络科技

## 规格参数
- 接口: 4个千兆WAN口 + 8个千兆LAN口
- 无线标准: Wi-Fi 6 (802.11ax)
- 最大吞吐量: 3.8Gbps
- 并发连接数: 2000
- VPN支持: IPsec/PPTP/L2TP
- 功耗: 25W

## 产品描述
RT-3800是一款企业级智能路由器，支持Wi-Fi 6标准，提供高速无线和有线网络连接。
适用于中小型企业办公网络，支持多WAN负载均衡和VPN隧道。

## 适用场景
- 中小型企业办公网络
- 分支机构互联
- 远程办公VPN接入
""",
        "metadata": {"doc_id": "KB-PRD-001", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-002",
        "title": "KB-PRD-002 企业交换机 SW-4800",
        "content": """# PRD-002 企业交换机 SW-4800

## 基本信息
- 产品编号: PRD-002
- 产品名称: 企业交换机 SW-4800
- 产品类别: 网络设备/交换机
- 单价: 5999元
- 供应商: 智能网络科技

## 规格参数
- 端口数: 48口千兆 + 4个万兆SFP+
- 背板带宽: 256Gbps
- 交换容量: 128Gbps
- MAC地址表: 16K
- VLAN支持: 4094个
- PoE功率: 370W
- 堆叠支持: 是

## 产品描述
SW-4800是一款高密度企业级三层交换机，支持48个千兆电口和4个万兆光口。
支持VLAN、ACL、QoS等企业级特性，适用于大型企业核心或汇聚层部署。

## 适用场景
- 企业核心/汇聚层
- 数据中心接入
- 园区网主干
""",
        "metadata": {"doc_id": "KB-PRD-002", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-003",
        "title": "KB-PRD-003 无线AP AP-1200",
        "content": """# PRD-003 无线AP AP-1200

## 基本信息
- 产品编号: PRD-003
- 产品名称: 无线AP AP-1200
- 产品类别: 网络设备/无线AP
- 单价: 1299元

## 规格参数
- 无线标准: Wi-Fi 6 (802.11ax)
- 并发用户: 200
- 覆盖半径: 50米
- PoE供电: 支持
- 安装方式: 吸顶/壁挂

## 产品描述
AP-1200是一款企业级无线接入点，支持Wi-Fi 6标准，可同时服务200个并发用户。
适合办公区、会议室等高密度无线覆盖场景。
""",
        "metadata": {"doc_id": "KB-PRD-003", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-004",
        "title": "KB-PRD-004 防火墙 FW-2000",
        "content": """# PRD-004 防火墙 FW-2000

## 基本信息
- 产品编号: PRD-004
- 产品名称: 下一代防火墙 FW-2000
- 产品类别: 安全设备/防火墙
- 单价: 15999元

## 规格参数
- 吞吐量: 2Gbps
- 并发连接: 500000
- IPSec VPN: 500条
- 接口: 8个千兆口
- 功能: IDS/IPS, AV, URL过滤, 应用识别

## 产品描述
FW-2000是一款下一代防火墙(NGFW)，集成了入侵检测/防御、防病毒、URL过滤和应用识别功能。
适用于企业网络边界安全防护。
""",
        "metadata": {"doc_id": "KB-PRD-004", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-005",
        "title": "KB-PRD-005 服务器 SRV-R520",
        "content": """# PRD-005 服务器 SRV-R520

## 基本信息
- 产品编号: PRD-005
- 产品名称: 机架式服务器 SRV-R520
- 产品类别: 计算设备/服务器
- 单价: 39999元

## 规格参数
- CPU: 双路 Intel Xeon Gold 6248R (3.0GHz, 24C/48T)
- 内存: 128GB DDR4 ECC
- 存储: 4x 2TB SAS (支持RAID 0/1/5/10)
- 网络: 双千兆网口
- 电源: 双路 800W 冗余
- 机箱: 2U机架

## 产品描述
SRV-R520是一款2U双路机架式服务器，适用于企业虚拟化、数据库和核心业务应用。
支持热插拔硬盘和冗余电源，确保业务连续性。
""",
        "metadata": {"doc_id": "KB-PRD-005", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-006",
        "title": "KB-PRD-006 存储阵列 STG-N800",
        "content": """# PRD-006 存储阵列 STG-N800

## 基本信息
- 产品编号: PRD-006
- 产品名称: NAS存储阵列 STG-N800
- 产品类别: 存储设备/NAS
- 单价: 25999元

## 规格参数
- 盘位: 8盘位
- 支持硬盘: SAS/SATA
- 最大容量: 128TB
- RAID: 0/1/5/6/10/50
- 网络: 双千兆 + 双万兆
- 协议: NFS, CIFS, iSCSI, FTP

## 产品描述
STG-N800是一款企业级NAS存储阵列，支持多种RAID级别和网络协议。
适用于文件共享、备份和虚拟化存储场景。
""",
        "metadata": {"doc_id": "KB-PRD-006", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-007",
        "title": "KB-PRD-007 负载均衡 LB-1000",
        "content": """# PRD-007 负载均衡器 LB-1000

## 基本信息
- 产品编号: PRD-007
- 产品名称: 应用负载均衡器 LB-1000
- 产品类别: 网络设备/负载均衡
- 单价: 19999元

## 规格参数
- 吞吐量: 1Gbps
- 并发连接: 200000
- SSL TPS: 5000
- 支持协议: HTTP/HTTPS/TCP/UDP
- 健康检查: 主动/被动
- 会话保持: Cookie/IP/Header

## 产品描述
LB-1000是一款高性能应用负载均衡器，支持L4-L7层负载分发。
适用于Web应用、数据库和微服务的流量分发和高可用保障。
""",
        "metadata": {"doc_id": "KB-PRD-007", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-008",
        "title": "KB-PRD-008 UPS电源 UPS-3000",
        "content": """# PRD-008 UPS不间断电源 UPS-3000

## 基本信息
- 产品编号: PRD-008
- 产品名称: 在线式UPS UPS-3000
- 产品类别: 电源设备/UPS
- 单价: 8999元

## 规格参数
- 功率: 3000VA/2700W
- 电池类型: 免维护铅酸电池
- 后备时间: 30分钟(满载)
- 转换时间: 0ms
- 接口: USB/RS232/SNMP
- 效率: >95%

## 产品描述
UPS-3000是一款在线双变换式UPS，为关键IT设备提供不间断电源保护。
支持SNMP网络管理，适用于机房和小型数据中心。
""",
        "metadata": {"doc_id": "KB-PRD-008", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-009",
        "title": "KB-PRD-009 视频会议 VC-500",
        "content": """# PRD-009 视频会议终端 VC-500

## 基本信息
- 产品编号: PRD-009
- 产品名称: 高清视频会议终端 VC-500
- 产品类别: 协作设备/视频会议
- 单价: 14999元

## 规格参数
- 视频分辨率: 4K/1080p
- 支持协议: H.323/SIP
- 摄像头: 4K PTZ
- 麦克风: 全向阵列(6米拾音)
- 接口: HDMI/USB/网络
- 兼容: Zoom/Teams/钉钉/飞书

## 产品描述
VC-500是一款企业级高清视频会议终端，支持4K分辨率和多种会议平台。
适用于中大型会议室，提供沉浸式远程协作体验。
""",
        "metadata": {"doc_id": "KB-PRD-009", "category": "产品查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-PRD-010",
        "title": "KB-PRD-010 网络管理系统 NMS-100",
        "content": """# PRD-010 网络管理系统 NMS-100

## 基本信息
- 产品编号: PRD-010
- 产品名称: 网络管理平台 NMS-100
- 产品类别: 软件平台/网络管理
- 单价: 49999元

## 规格参数
- 管理节点: 最多1000个
- 支持协议: SNMP v1/v2c/v3, NetFlow, sFlow
- 功能: 拓扑发现, 告警, 报表, 配置管理
- 部署: 虚拟化/物理机
- 数据库: 内置PostgreSQL

## 产品描述
NMS-100是一款企业级网络管理平台，提供网络设备监控、告警、配置和报表功能。
支持多厂商设备统一管理，适用于大型园区网和数据中心网络运维。
""",
        "metadata": {"doc_id": "KB-PRD-010", "category": "产品查询"},
    },
    # -- Customer documents (KB-CUS-001..005) --
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-CUS-001",
        "title": "KB-CUS-001 客户档案 智联科技有限公司",
        "content": """# CUS-001 智联科技有限公司

## 客户基本信息
- 客户编号: CUS-001
- 客户名称: 智联科技有限公司
- 行业: 互联网/科技
- 规模: 200-500人
- 信用等级: A
- 合作年限: 5年

## 联系方式
- 联系人: 张经理
- 电话: 138-0000-0001
- 邮箱: zhang@zhilian-tech.com
- 地址: 北京市海淀区中关村软件园

## 客户画像
智联科技是一家快速成长的互联网公司，主要业务为云计算和SaaS服务。
网络基础设施需求旺盛，是公司路由器和交换机产品的重点客户。

## 历史订单
- 2024-Q1: 采购RT-3800路由器20台
- 2024-Q2: 采购SW-4800交换机5台
- 2024-Q3: 采购AP-1200无线AP 30台
""",
        "metadata": {"doc_id": "KB-CUS-001", "category": "客户查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-CUS-002",
        "title": "KB-CUS-002 客户档案 华信制造集团",
        "content": """# CUS-002 华信制造集团

## 客户基本信息
- 客户编号: CUS-002
- 客户名称: 华信制造集团
- 行业: 制造业
- 规模: 1000-5000人
- 信用等级: AA
- 合作年限: 8年

## 联系方式
- 联系人: 李总
- 电话: 139-0000-0002
- 邮箱: li@huaxin-mfg.com
- 地址: 上海市浦东新区张江高科

## 客户画像
华信制造是大型制造业企业，拥有多个工厂和研发中心。
对网络安全和稳定要求高，是防火墙和服务器的重点客户。
""",
        "metadata": {"doc_id": "KB-CUS-002", "category": "客户查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-CUS-003",
        "title": "KB-CUS-003 客户档案 启航教育集团",
        "content": """# CUS-003 启航教育集团

## 客户基本信息
- 客户编号: CUS-003
- 客户名称: 启航教育集团
- 行业: 教育
- 规模: 500-1000人
- 信用等级: A
- 合作年限: 3年

## 联系方式
- 联系人: 王主任
- 电话: 137-0000-0003
- 邮箱: wang@qihang-edu.com
- 地址: 广州市天河区五山路

## 客户画像
启航教育是一家综合性教育集团，拥有多所学校和培训中心。
无线覆盖和视频会议需求大，是AP和视频会议终端的重要客户。
""",
        "metadata": {"doc_id": "KB-CUS-003", "category": "客户查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-CUS-004",
        "title": "KB-CUS-004 客户档案 仁济医院",
        "content": """# CUS-004 仁济医院

## 客户基本信息
- 客户编号: CUS-004
- 客户名称: 仁济医院
- 行业: 医疗
- 规模: 1000-5000人
- 信用等级: AAA
- 合作年限: 6年

## 联系方式
- 联系人: 赵科长
- 电话: 136-0000-0004
- 邮箱: zhao@renji-hosp.com
- 地址: 上海市黄浦区山东中路

## 客户画像
仁济医院是三甲综合医院，信息化建设成熟。
对数据安全和存储要求极高，是防火墙、存储阵列和UPS的重点客户。
""",
        "metadata": {"doc_id": "KB-CUS-004", "category": "客户查询"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-CUS-005",
        "title": "KB-CUS-005 客户档案 云海金融",
        "content": """# CUS-005 云海金融服务有限公司

## 客户基本信息
- 客户编号: CUS-005
- 客户名称: 云海金融服务有限公司
- 行业: 金融
- 规模: 200-500人
- 信用等级: AAA
- 合作年限: 4年

## 联系方式
- 联系人: 孙总监
- 电话: 135-0000-0005
- 邮箱: sun@yunhai-fin.com
- 地址: 深圳市福田区中心区

## 客户画像
云海金融是一家创新型金融服务公司，核心系统对可用性要求99.99%。
是负载均衡、服务器和NMS管理平台的高价值客户。
""",
        "metadata": {"doc_id": "KB-CUS-005", "category": "客户查询"},
    },
    # -- Policy documents --
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-POL-001",
        "title": "KB-POL-001 订单管理制度",
        "content": """# 订单管理制度

## 1. 订单创建
- 所有销售订单必须通过ERP系统创建
- 正常订单（金额≤10000元且数量≤100）：直接创建，无需审批
- 高价值订单（金额>10000元或数量>100）：需提交审批，经部门经理审批通过后方可执行

## 2. 订单审批
- 审批流程：创建人提交 → 部门经理审批 → 财务复核（金额>50000元时）
- 审批时限：常规24小时内，紧急4小时内
- 审批通过后不可撤销，如需取消须走退货流程

## 3. 订单修改与取消
- 已创建未审批：创建人可直接修改或取消
- 已审批未执行：需审批人同意后方可修改
- 已执行订单：不可修改，如需变更走退货/换货流程

## 4. 退货政策
- 质量问题：7天内全额退货
- 非质量问题：需经销售总监审批，可能收取15%手续费
- 定制产品：不支持退货

## 5. 幂等性要求
- 同一客户同一产品的重复订单请求，系统应自动识别并跳过
- 幂等键由：租户ID + 会话ID + 工具名 + 参数哈希 组成
""",
        "metadata": {"doc_id": "KB-POL-001", "category": "制度政策"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-POL-002",
        "title": "KB-POL-002 产品退换货政策",
        "content": """# 产品退换货政策

## 1. 退货条件
- 到货7天内：因质量问题可无条件退货
- 到货15天内：非质量问题需说明原因，经审批后可退
- 超过15天：不支持退货，仅支持维修

## 2. 换货政策
- 到货30天内：因产品规格不符可申请换货
- 换货差价多退少补
- 定制产品不支持换货

## 3. 退货流程
1. 客户提交退货申请
2. 销售经理审核
3. 仓库确认收货
4. 财务退款（3-5个工作日）

## 4. 特殊说明
- 所有退货产品须保持原包装完整
- 配件齐全，不影响二次销售
- 软件产品一经激活不支持退货
""",
        "metadata": {"doc_id": "KB-POL-002", "category": "制度政策"},
    },
    {
        "source_type": "markdown",
        "source_uri": "kb://KB-POL-003",
        "title": "KB-POL-003 客户信用管理",
        "content": """# 客户信用管理制度

## 1. 信用等级
- AAA: 最高信用，可赊账100万，账期90天
- AA: 优秀信用，可赊账50万，账期60天
- A: 良好信用，可赊账20万，账期30天
- B: 一般信用，需预付50%定金
- C: 低信用，需款到发货

## 2. 信用评估
- 新客户默认B级
- 合作满1年且无不良记录可申请升级
- 存在逾期账款的客户自动降级

## 3. 账期管理
- 到期前7天系统自动发送提醒
- 逾期15天暂停新订单
- 逾期30天转入法务催收

## 4. 客户分类
- 战略客户：年采购额>100万，享受VIP价格
- 重点客户：年采购额30-100万
- 一般客户：年采购额<30万
""",
        "metadata": {"doc_id": "KB-POL-003", "category": "制度政策"},
    },
]


async def main():
    print("Logging in as admin...")
    async with httpx.AsyncClient(timeout=120) as client:
        token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not token:
            print("ERROR: Failed to login as admin")
            return

        headers = {"Authorization": f"Bearer {token}"}
        print(f"Logged in. Seeding {len(DOCUMENTS)} knowledge documents...")

        success = 0
        failed = 0
        for i, doc in enumerate(DOCUMENTS):
            try:
                resp = await client.post(
                    f"{API_BASE}/api/admin/knowledge/documents",
                    headers=headers,
                    json=doc,
                    timeout=60,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    chunk_count = data.get("chunk_count", "?")
                    print(f"  [{i+1}/{len(DOCUMENTS)}] ✓ {doc['title']} ({chunk_count} chunks)")
                    success += 1
                else:
                    print(f"  [{i+1}/{len(DOCUMENTS)}] ✗ {doc['title']}: HTTP {resp.status_code} {resp.text[:100]}")
                    failed += 1
            except Exception as e:
                print(f"  [{i+1}/{len(DOCUMENTS)}] ✗ {doc['title']}: {e}")
                failed += 1

        print(f"\nDone: {success} succeeded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
