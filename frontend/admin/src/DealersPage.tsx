import { Button, Card, Modal, Space, Table, Tag, Typography } from "antd";
import type { Dealer, DealerAccount, Store } from "./types";

const statusColor = (value: string) =>
  value === "active" ? "success" : value === "frozen" ? "error" : "default";

export function DealersPage({
  dealers,
  dealerAccounts,
  stores,
  onCreateDealer,
  onSetupDealerAccount,
  onFreezeDealer,
  onUnfreezeDealer,
  onDeleteDealer,
  refresh,
}: {
  dealers: Dealer[];
  dealerAccounts: DealerAccount[];
  stores: Store[];
  onCreateDealer: () => void;
  onSetupDealerAccount: (dealer: Dealer) => void;
  onFreezeDealer: (dealer: Dealer) => Promise<void>;
  onUnfreezeDealer: (dealer: Dealer) => Promise<void>;
  onDeleteDealer: (dealer: Dealer) => Promise<void>;
  refresh: () => Promise<void>;
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>经销商</Typography.Title>
          <Typography.Text type="secondary">
            流向记录仅供统计，不影响抽奖资格
          </Typography.Text>
        </div>
        <Button type="primary" onClick={onCreateDealer}>
          新建经销商
        </Button>
      </div>
      <Card>
        <Table
          rowKey="id"
          dataSource={dealers}
          columns={[
            { title: "编号", dataIndex: "dealer_no" },
            { title: "公司名称", dataIndex: "company_name" },
            { title: "联系人", dataIndex: "contact_name" },
            {
              title: "区域",
              render: (_, item: Dealer) => `${item.province} ${item.city}`,
            },
            {
              title: "等级",
              dataIndex: "level",
              render: (value) => `L${value}`,
            },
            {
              title: "状态",
              dataIndex: "status",
              render: (value) => (
                <Tag color={statusColor(value)}>
                  {value === "active" ? "正常" : "冻结"}
                </Tag>
              ),
            },
            {
              title: "登录账号",
              render: (_, item: Dealer) => dealerAccounts.find((account) => account.dealer_id === item.id)?.username ?? "未设置",
            },
            {
              title: "操作",
              render: (_, item: Dealer) => {
                const account = dealerAccounts.find((entry) => entry.dealer_id === item.id);
                return <Space size={4}>
                  <Button size="small" type={account ? "default" : "primary"} onClick={() => onSetupDealerAccount(item)}>{account ? "重置密码" : "设置密码"}</Button>
                  {item.status === "active" ? <Button size="small" danger onClick={() => onFreezeDealer(item)}>冻结</Button> : <><Button size="small" type="primary" onClick={() => onUnfreezeDealer(item)}>恢复</Button><Button size="small" danger onClick={() => onDeleteDealer(item)}>删除</Button></>}
                </Space>;
              },
            },
          ]}
        />
      </Card>
    </>
  );
}
