import { Card, Statistic, Table, Tag, Typography } from "antd";
import type { Activity, Batch, Dealer, DashboardMetrics } from "./types";

const statusColor = (value: string) =>
  value === "active" ? "success" : value === "frozen" ? "error" : "default";
const localDateTime = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

export function OverviewPage({
  dealers,
  batches,
  activities,
  metrics,
}: {
  dealers: Dealer[];
  batches: Batch[];
  activities: Activity[];
  metrics?: DashboardMetrics;
}) {
  const activeCodes = batches
    .filter((item) => item.status === "active")
    .reduce((sum, item) => sum + item.total_count, 0);
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>经营概览</Typography.Title>
          <Typography.Text type="secondary">
            运营数据来自 MySQL 实时配置
          </Typography.Text>
        </div>
      </div>
      <div className="metrics">
        <Card>
          <Statistic
            title="已生成二维码"
            value={batches.reduce((sum, item) => sum + item.total_count, 0)}
            suffix="个"
          />
        </Card>
        <Card>
          <Statistic
            title="已激活二维码"
            value={activeCodes}
            suffix="个"
            valueStyle={{ color: "#137a50" }}
          />
        </Card>
        <Card>
          <Statistic
            title="正常经销商"
            value={dealers.filter((item) => item.status === "active").length}
            suffix="家"
          />
        </Card>
        <Card>
          <Statistic
            title="累计抽奖"
            value={metrics?.draw_count ?? 0}
            suffix="次"
          />
        </Card>
      </div>
      <div className="metrics">
        <Card>
          <Statistic title="中奖次数" value={metrics?.winner_count ?? 0} suffix="次" />
        </Card>
        <Card>
          <Statistic title="累计现金奖" value={Number(metrics?.cash_issued ?? 0)} precision={2} prefix="¥" />
        </Card>
        <Card>
          <Statistic title="待兑换" value={metrics?.pending_redemptions ?? 0} suffix="笔" />
        </Card>
        <Card>
          <Statistic title="进行中活动" value={activities.filter((item) => item.status === "active").length} suffix="场" />
        </Card>
      </div>
      <section className="dashboard-grid">
        <Card title="活动状态">
          <Table
            size="small"
            pagination={false}
            rowKey="id"
            dataSource={activities.slice(0, 6)}
            columns={[
              { title: "活动", dataIndex: "name" },
              {
                title: "时间",
                render: (_, row: Activity) =>
                  `${localDateTime(row.start_at)} 至 ${localDateTime(row.end_at)}`,
              },
              {
                title: "状态",
                dataIndex: "status",
                render: (value) => (
                  <Tag color={statusColor(value)}>
                    {value === "active" ? "进行中" : "草稿"}
                  </Tag>
                ),
              },
            ]}
          />
        </Card>
      </section>
    </>
  );
}
