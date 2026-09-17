import { Button, Card, Space, Table, Tag, Typography } from "antd";
import type { ConsumerSummary } from "./types";

const localDateTime = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

export function ConsumersPage({
  consumers,
  onViewDetail,
  onViewRecords,
  onSetProbability,
  onToggleRisk,
  onAnonymize,
}: {
  consumers: ConsumerSummary[];
  onViewDetail: (consumer: ConsumerSummary) => void;
  onViewRecords: (consumer: ConsumerSummary) => void;
  onSetProbability: (consumer: ConsumerSummary) => void;
  onToggleRisk: (consumer: ConsumerSummary) => Promise<void>;
  onAnonymize: (consumer: ConsumerSummary) => Promise<void>;
}) {
  return <>
    <div className="page-heading">
      <div>
        <Typography.Title level={2}>用户档案与风控</Typography.Title>
        <Typography.Text type="secondary">查看参与记录，并可调整中奖系数或拦截异常用户。</Typography.Text>
      </div>
    </div>
    <Card>
      <Table rowKey="id" dataSource={consumers} locale={{ emptyText: "暂无用户" }} columns={[
        { title: "微信用户", render: (_, item: ConsumerSummary) => <><b>{item.nickname}</b><br /><Typography.Text type="secondary">OpenID：{item.openid}</Typography.Text></> },
        { title: "参与 / 中奖", render: (_, item: ConsumerSummary) => `${item.draw_count} / ${item.win_count} 次` },
        { title: "中奖系数", render: (_, item: ConsumerSummary) => `${(Number(item.win_probability_multiplier) * 100).toFixed(2)}%` },
        { title: "状态", render: (_, item: ConsumerSummary) => <><Tag color={item.risk_status === "blocked" ? "error" : "success"}>{item.risk_status === "blocked" ? "已拦截" : "正常"}</Tag>{item.risk_note && <Typography.Text type="secondary"> {item.risk_note}</Typography.Text>}</> },
        { title: "最近抽奖", dataIndex: "last_draw_at", render: localDateTime },
        { title: "注册时间", dataIndex: "created_at", render: localDateTime },
        { title: "操作", render: (_, item: ConsumerSummary) => <Space size={4}>
          <Button size="small" onClick={() => onViewDetail(item)}>用户详情</Button>
          <Button size="small" onClick={() => onViewRecords(item)}>查看记录</Button>
          <Button size="small" onClick={() => onSetProbability(item)}>中奖系数</Button>
          <Button size="small" danger={item.risk_status !== "blocked"} onClick={() => onToggleRisk(item)}>{item.risk_status === "blocked" ? "恢复" : "拦截"}</Button>
          {!item.anonymized_at && <Button size="small" danger onClick={() => onAnonymize(item)}>匿名化</Button>}
        </Space> }
      ]} />
    </Card>
  </>;
}
