import { Button, Card, Input, Pagination, Select, Space, Table, Tag, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { WinnerPage, WinnerRecord } from "./types";

const localDateTime = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

export function RecordsPage({
  winnerData,
  winnerPage,
  recordKeyword,
  recordStatus,
  onPageChange,
  onSearch,
  onStatusChange,
  onExport,
}: {
  winnerData: WinnerPage;
  winnerPage: number;
  recordKeyword: string;
  recordStatus?: string;
  onPageChange: (page: number) => void;
  onSearch: (keyword: string) => void;
  onStatusChange: (status: string | undefined) => void;
  onExport: () => void;
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>中奖记录</Typography.Title>
          <Typography.Text type="secondary">
            每笔记录保存微信用户、OpenID、IP、授权定位及天地图解析后的省市区地址。
          </Typography.Text>
        </div>
        <Space>
          <Input.Search allowClear placeholder="姓名、抽奖号、兑换码或奖品" style={{ width: 240 }} onSearch={onSearch} />
          <Select allowClear placeholder="兑换状态" style={{ width: 130 }} options={[{ value: "pending", label: "待兑换" }, { value: "redeemed", label: "已兑换" }, { value: "expired", label: "已过期" }, { value: "not_required", label: "无需兑换" }]} onChange={onStatusChange} />
          <Button icon={<DownloadOutlined />} onClick={onExport}>导出</Button>
        </Space>
      </div>
      <Card>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={winnerData.items}
          locale={{ emptyText: "暂无中奖记录" }}
          columns={[
            {
              title: "抽奖用户",
              render: (_, item: WinnerRecord) => (
                <>
                  <b>{item.consumer_name}</b>
                  <br />
                  <Typography.Text type="secondary">
                    OpenID：{item.consumer_openid}
                  </Typography.Text>
                </>
              ),
            },
            {
              title: "请求来源",
              render: (_, item: WinnerRecord) => (
                <>
                  <Typography.Text>{item.client_ip || "未采集"}</Typography.Text>
                  <br />
                  <Typography.Text type="secondary">{[item.client_province, item.client_city, item.client_district].filter(Boolean).join(" ") || item.client_address || item.client_location || "未授权定位"}</Typography.Text>
                </>
              ),
            },
            { title: "抽奖号码 / 批次", render: (_, item: WinnerRecord) => <>{item.code_no}<br /><Typography.Text type="secondary">{item.batch_no}</Typography.Text></> },
            { title: "流向经销商", render: (_, item: WinnerRecord) => <>{item.dealer_name}<br />{item.distribution_no && <Typography.Text type="secondary">{item.distribution_no}</Typography.Text>}</> },
            { title: "活动", dataIndex: "activity_name" },
            {
              title: "奖品",
              render: (_, item: WinnerRecord) =>
                item.prize_type === "cash"
                  ? `${item.prize_name} ¥${Number(item.amount).toFixed(2)}`
                  : item.prize_name,
            },
            { title: "履约凭证", render: (_, item: WinnerRecord) => item.cash_order_no ? <><Tag color="blue">微信现金订单 · {item.payment_status === "success" ? "已到账" : item.payment_status === "failed" ? "发放失败" : "处理中"}</Tag><br /><Typography.Text copyable code>{item.cash_order_no}</Typography.Text></> : item.upgrade_redeem_code ? <><Tag color="gold">换购码 · {item.upgrade_status === "redeemed" ? "已核销" : "待核销"}</Tag><br /><Typography.Text copyable code>{item.upgrade_redeem_code}</Typography.Text><br /><Typography.Text type="secondary">补差价 ¥{Number(item.upgrade_amount || 0).toFixed(2)}</Typography.Text></> : "-" },
            { title: "命中策略", render: (_, item: WinnerRecord) => item.lottery_policy_scope === "default" ? "默认奖池" : `${item.lottery_policy_scope || "默认"} v${item.lottery_policy_version ?? "-"}` },
            {
              title: "抽奖时间",
              dataIndex: "drawn_at",
              render: localDateTime,
            },
            {
              title: "兑换状态",
              render: (_, item: WinnerRecord) => (
                <Tag
                  color={
                    item.redemption_status === "redeemed"
                      ? "success"
                      : item.redemption_status === "pending"
                        ? "warning"
                        : "default"
                  }
                >
                  {item.redemption_status === "redeemed"
                    ? "已兑换"
                    : item.redemption_status === "pending"
                      ? "待兑换"
                      : item.redemption_status === "expired"
                        ? "已过期"
                        : "无需兑换"}
                </Tag>
              ),
            },
          ]}
        />
        <div className="code-pagination">
          <Typography.Text type="secondary">
            共 {winnerData.total} 条记录
          </Typography.Text>
          <Pagination
            current={winnerPage}
            pageSize={20}
            total={winnerData.total}
            showSizeChanger={false}
            onChange={onPageChange}
          />
        </div>
      </Card>
    </>
  );
}
