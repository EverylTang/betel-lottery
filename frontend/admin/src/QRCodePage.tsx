import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { Batch, Dealer, Distribution } from "./types";

export function QRCodePage({
  batches,
  dealers,
  distributions,
  exporting,
  onCreateBatch,
  onExportBatch,
  onActivateBatch,
  onViewBatchCodes,
  onCreateDistribution,
  onRefreshDistributions,
}: {
  batches: Batch[];
  dealers: Dealer[];
  distributions: Distribution[];
  exporting: number | null;
  onCreateBatch: () => void;
  onExportBatch: (batch: Batch, type: "csv" | "zip") => void;
  onActivateBatch: (batch: Batch) => Promise<void>;
  onViewBatchCodes: (batch: Batch) => void;
  onCreateDistribution: () => void;
  onRefreshDistributions: () => void;
}) {
  const localDateTime = (value: string) => value ? new Date(value).toLocaleString("zh-CN") : "-";

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>二维码与流向</Typography.Title>
          <Typography.Text type="secondary">
            每个批次同时生成袋外二维码和袋内 4 位兑奖码；导出印刷文件后，再登记交付给经销商的完整批次或印刷号码段。
          </Typography.Text>
        </div>
      </div>
      <Card className="distribution-card" title="流向登记记录" extra={<Space><Button onClick={onRefreshDistributions}>刷新记录</Button><Button type="primary" onClick={onCreateDistribution}>登记流向</Button></Space>}>
        <Table
          size="small"
          rowKey="id"
          dataSource={distributions}
          locale={{ emptyText: "暂无流向记录，可登记整个二维码批次或指定印刷号码段" }}
          columns={[
            { title: "流向单号", dataIndex: "distribution_no" },
            { title: "二维码批次", dataIndex: "batch_no" },
            { title: "经销商", dataIndex: "dealer_name" },
            { title: "印刷号码段", render: (_, item: Distribution) => `${item.start_code_no} - ${item.end_code_no}` },
            { title: "数量", dataIndex: "quantity" },
            { title: "状态", dataIndex: "status", render: (value: string) => <Tag color={value === "effective" ? "success" : "default"}>{value === "effective" ? "已登记" : value}</Tag> },
            { title: "登记时间", dataIndex: "recorded_at", render: localDateTime },
          ]}
        />
      </Card>
      <Card className="code-list-card" title="二维码批次" extra={<Button type="primary" onClick={onCreateBatch}>创建抽奖批次</Button>}>
        <Table
          rowKey="id"
          dataSource={batches}
          columns={[
            { title: "批次号", dataIndex: "batch_no" },
            { title: "批次名称", dataIndex: "batch_name" },
            { title: "码前缀", dataIndex: "prefix" },
            { title: "数量", dataIndex: "total_count" },
            { title: "抽奖凭证", render: () => <Tag color="gold">袋外二维码 + 袋内 4 位码</Tag> },
            {
              title: "状态",
              dataIndex: "activation_status",
              render: (value: Batch["activation_status"], item: Batch) => (
                <Tag color={value === "active" ? "success" : value === "partial" ? "processing" : "default"}>
                  {value === "active" ? "已激活" : value === "partial" ? `部分激活（剩余 ${item.inactive_count}）` : "待激活"}
                </Tag>
              ),
            },
            {
              title: "导出",
              render: (_, item: Batch) => (
                <Space size={4}>
                  <Button
                    size="small"
                    loading={exporting === item.id}
                    onClick={() => onExportBatch(item, "csv")}
                  >
                    印刷清单 CSV
                  </Button>
                  <Button
                    icon={<DownloadOutlined />}
                    size="small"
                    loading={exporting === item.id}
                    onClick={() => onExportBatch(item, "zip")}
                  >
                    印刷包 ZIP
                  </Button>
                </Space>
              ),
            },
            {
              title: "操作",
              render: (_, item: Batch) => (
                <Space size={4}>
                  <Button
                    size="small"
                    onClick={() => onViewBatchCodes(item)}
                  >
                    查看号码
                  </Button>
                  <Button
                    size="small"
                    type={item.activation_status === "active" ? "default" : "primary"}
                    disabled={item.activation_status === "active"}
                    onClick={() => onActivateBatch(item)}
                  >
                    {item.activation_status === "active" ? "已激活" : item.can_activate ? "激活" : "缺少生效活动"}
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
