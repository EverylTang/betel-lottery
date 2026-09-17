import { Button, Card, Space, Table, Tag, Typography } from "antd";
import type { Dealer, Store } from "./types";

export function StoresPage({
  dealers,
  stores,
  onCreateStore,
  onResetStorePin,
  onToggleStoreStatus,
}: {
  dealers: Dealer[];
  stores: Store[];
  onCreateStore: () => void;
  onResetStorePin: (store: Store) => void;
  onToggleStoreStatus: (store: Store) => Promise<void>;
}) {
  return <>
    <div className="page-heading">
      <div>
        <Typography.Title level={2}>门店管理</Typography.Title>
        <Typography.Text type="secondary">维护门店核销 PIN 与启停状态。停用后门店不能核销换购码。</Typography.Text>
      </div>
      <Button type="primary" onClick={onCreateStore}>新建门店</Button>
    </div>
    <Card>
      <Table rowKey="id" dataSource={stores} locale={{ emptyText: "暂无门店" }} columns={[
        { title: "门店编号", dataIndex: "store_no" },
        { title: "门店名称", dataIndex: "name" },
        { title: "所属经销商", render: (_, item: Store) => dealers.find((dealer) => dealer.id === item.dealer_id)?.company_name || `#${item.dealer_id}` },
        { title: "联系人", render: (_, item: Store) => `${item.contact_name} / ${item.phone}` },
        { title: "状态", render: (_, item: Store) => <Tag color={item.status === "active" ? "success" : "default"}>{item.status === "active" ? "启用" : "停用"}</Tag> },
        { title: "操作", render: (_, item: Store) => <Space size={4}><Button size="small" onClick={() => onResetStorePin(item)}>重置 PIN</Button><Button size="small" danger={item.status === "active"} onClick={() => onToggleStoreStatus(item)}>{item.status === "active" ? "停用" : "启用"}</Button></Space> },
      ]} />
    </Card>
  </>;
}
