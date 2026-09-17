import { Button, Card, Table, Tag, Typography } from "antd";
import type { AdminAccount, AuditLog } from "./types";

const localDateTime = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

export function SystemPage({
  adminAccounts,
  auditLogs,
  onCreateAdmin,
  onToggleAdminStatus,
}: {
  adminAccounts: AdminAccount[];
  auditLogs: AuditLog[];
  onCreateAdmin: () => void;
  onToggleAdminStatus: (account: AdminAccount) => Promise<void>;
}) {
  return <>
    <div className="page-heading">
      <div>
        <Typography.Title level={2}>系统管理</Typography.Title>
        <Typography.Text type="secondary">账号启停和关键运营操作均留存审计记录。</Typography.Text>
      </div>
      <Button type="primary" onClick={onCreateAdmin}>新建运营账号</Button>
    </div>
    <section className="dashboard-grid">
      <Card title="后台账号">
        <Table size="small" rowKey="id" pagination={false} dataSource={adminAccounts} columns={[
          { title: "账号", dataIndex: "username" },
          { title: "角色", dataIndex: "role", render: (value) => value === "super_admin" ? "超级管理员" : "运营员" },
          { title: "状态", render: (_, item: AdminAccount) => <Tag color={item.is_active ? "success" : "default"}>{item.is_active ? "启用" : "停用"}</Tag> },
          { title: "操作", render: (_, item: AdminAccount) => <Button size="small" disabled={item.username === "admin" && item.is_active} onClick={() => onToggleAdminStatus(item)}>{item.is_active ? "停用" : "启用"}</Button> }
        ]} />
      </Card>
      <Card title="最近审计日志">
        <Table size="small" rowKey="id" pagination={false} dataSource={auditLogs} columns={[
          { title: "时间", dataIndex: "created_at", render: localDateTime },
          { title: "操作", dataIndex: "action" },
          { title: "对象", render: (_, item: AuditLog) => `${item.target_type} #${item.target_id ?? "-"}` },
          { title: "详情", dataIndex: "detail" }
        ]} />
      </Card>
    </section>
  </>;
}
