import { Button, Card, InputNumber, Select, Space, Table, Tag, Typography } from "antd";
import type { Activity, ActivityConfiguration, Batch, Prize } from "./types";

const statusColor = (value: string) =>
  value === "active" ? "success" : value === "frozen" ? "error" : "default";

export function ActivitiesPage({
  batches,
  prizes,
  activities,
  selectedBatchId,
  selectedActivityId,
  configuration,
  onSelectBatch,
  onSelectActivity,
  onOpenPrizesPage,
  onOpenPoliciesPage,
  onCreateActivity,
  onEditActivity,
  onToggleActivityStatus,
  onDeleteActivity,
  onAddPool,
  onEditPool,
  onRemovePool,
  onLinkBatch,
  refresh,
  refreshConfiguration,
}: {
  batches: Batch[];
  prizes: Prize[];
  activities: Activity[];
  selectedBatchId?: number;
  selectedActivityId?: number;
  configuration?: ActivityConfiguration;
  onSelectBatch: (id: number) => void;
  onSelectActivity: (id: number) => void;
  onOpenPrizesPage: () => void;
  onOpenPoliciesPage: () => void;
  onCreateActivity: () => void;
  onEditActivity: (activity: Activity) => void;
  onToggleActivityStatus: (activity: Activity) => Promise<void>;
  onDeleteActivity: (activity: Activity) => Promise<void>;
  onAddPool: () => void;
  onEditPool: (pool: ActivityConfiguration["prize_pools"][number]) => void;
  onRemovePool: (pool: ActivityConfiguration["prize_pools"][number]) => void;
  onLinkBatch: () => void;
  refresh: () => Promise<void>;
  refreshConfiguration: () => Promise<void>;
}) {
  const selectedActivity = activities.find((item) => item.id === selectedActivityId);

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>活动与默认奖池</Typography.Title>
          <Typography.Text type="secondary">
            配置活动时间、活动状态、二维码批次和全局默认奖池。
          </Typography.Text>
        </div>
        <Space>
          <Button onClick={onOpenPrizesPage}>管理奖品</Button>
          <Button type="primary" onClick={onCreateActivity}>
            创建活动
          </Button>
        </Space>
      </div>
      <section className="activity-list">
        <Card title="活动列表">
          <Table
            size="small"
            pagination={false}
            rowKey="id"
            dataSource={activities}
            rowClassName={(item) =>
              item.id === selectedActivityId ? "selected-row" : ""
            }
            onRow={(item) => ({
              onClick: () => onSelectActivity(item.id),
            })}
            columns={[
              { title: "活动名称", dataIndex: "name" },
              {
                title: "开始时间",
                dataIndex: "start_at",
                render: (value: string) => value ? new Date(value).toLocaleString("zh-CN") : "-",
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
              {
                title: "操作",
                render: (_, item: Activity) => (
                  <Space size={4} onClick={(event) => event.stopPropagation()}>
                    <Button size="small" onClick={() => onEditActivity(item)}>编辑</Button>
                    <Button size="small" danger={item.status === "active"} type={item.status === "active" ? "default" : "primary"} onClick={() => onToggleActivityStatus(item)}>{item.status === "active" ? "停用" : "启用"}</Button>
                    {item.status !== "active" && <Button size="small" danger onClick={() => onDeleteActivity(item)}>删除</Button>}
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      </section>
      <Card
        className="activity-config"
        title="活动默认奖池"
        extra={
          <Space>
            <Select
              value={selectedActivityId}
              placeholder="选择活动"
              style={{ width: 210 }}
              options={activities.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              onChange={onSelectActivity}
            />
            <Button
              disabled={!selectedActivityId}
              type="primary"
              onClick={onAddPool}
            >
              加入奖池
            </Button>
            <Button disabled={!selectedActivityId} onClick={onOpenPoliciesPage}>
              管理区域策略
            </Button>
          </Space>
        }
      >
        <div className="config-summary">
          <span>适用范围：{configuration?.activity?.all_batches ? "所有已激活批次" : `已关联 ${configuration?.batch_ids.length ?? 0} 个批次`}</span>
          <span>
            奖池概率：
            {(
              configuration?.prize_pools.reduce(
                (total, item) => total + Number(item.probability),
                0,
              ) ?? 0
            ).toFixed(2)}
          </span>
        </div>
        {selectedActivityId && !configuration?.activity?.all_batches && <Space style={{ marginBottom: 12 }}>
          <Select value={selectedBatchId} placeholder="选择要关联的批次" style={{ width: 240 }} options={batches.filter((batch) => !configuration?.batch_ids.includes(batch.id)).map((batch) => ({ value: batch.id, label: `${batch.batch_no} · ${batch.batch_name}` }))} onChange={onSelectBatch} />
          <Button disabled={!selectedBatchId} onClick={onLinkBatch}>关联批次</Button>
        </Space>}
        <Table
          size="small"
          pagination={false}
          rowKey="id"
          dataSource={configuration?.prize_pools ?? []}
          locale={{
            emptyText: selectedActivityId ? "尚未配置奖池" : "请先创建活动",
          }}
          columns={[
            { title: "奖品", dataIndex: "prize_name" },
            {
              title: "概率",
              dataIndex: "probability",
              render: (value) => `${(Number(value) * 100).toFixed(2)}%`,
            },
            {
              title: "日限额",
              dataIndex: "daily_limit",
              render: (value) => value || "不限",
            },
            { title: "今日已发", dataIndex: "issued_today" },
            {
              title: "库存",
              render: (
                _,
                item: ActivityConfiguration["prize_pools"][number],
              ) =>
                item.prize_type === "none"
                  ? "-"
                  : `${item.issued_stock}/${item.total_stock}`,
            },
            {
              title: "操作",
              render: (_, item: ActivityConfiguration["prize_pools"][number]) => (
                <Space size={4}>
                  <Button size="small" onClick={() => onEditPool(item)}>编辑</Button>
                  <Button danger size="small" onClick={() => onRemovePool(item)}>移除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
