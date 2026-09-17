import { useEffect, useMemo, useState } from "react";
import "@ant-design/v5-patch-for-react-19";
import { createRoot } from "react-dom/client";
import {
  App,
  Button,
  Card,
  Checkbox,
  ConfigProvider,
  Divider,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Pagination,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { MenuProps } from "antd";
import {
  AppstoreOutlined,
  DownloadOutlined,
  GiftOutlined,
  LineChartOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SettingOutlined,
  ShopOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { ApiError, download, request, setAdminToken } from "./api";
import { OverviewPage } from "./OverviewPage";
import { ActivitiesPage } from "./ActivitiesPage";
import { DealersPage } from "./DealersPage";
import { StoresPage } from "./StoresPage";
import { QRCodePage } from "./QRCodePage";
import { RecordsPage } from "./RecordsPage";
import { ConsumersPage } from "./ConsumersPage";
import { SystemPage } from "./SystemPage";
import type {
  Activity,
  ActivityConfiguration,
  AdminAccount,
  AuditLog,
  Batch,
  CodePage,
  ConsumerSummary,
  Dealer,
  DealerAccount,
  DashboardMetrics,
  Distribution,
  LotteryCode,
  Prize,
  Store,
  TrendPoint,
  WinnerPage,
  WinnerRecord,
} from "./types";
import "./styles.css";

const pageLabels: Record<string, string> = {
  overview: "经营概览", activities: "活动与默认奖池", policies: "区域策略", prizes: "奖品库",
  dealers: "经销商", stores: "门店管理", codes: "二维码与流向", codeNumbers: "抽奖号码列表", records: "中奖履约",
  consumers: "用户档案与风控", analytics: "数据分析", system: "系统管理",
};
const nav: MenuProps["items"] = [
  { key: "overview", icon: <AppstoreOutlined />, label: "经营概览" },
  { type: "group", label: "活动运营", children: [
    { key: "activities", icon: <GiftOutlined />, label: "活动与默认奖池" },
    { key: "policies", icon: <SettingOutlined />, label: "区域策略" },
    { key: "prizes", icon: <GiftOutlined />, label: "奖品库" },
  ] },
  { type: "group", label: "渠道与资源", children: [
    { key: "dealers", icon: <ShopOutlined />, label: "经销商" },
    { key: "stores", icon: <ShopOutlined />, label: "门店管理" },
    { key: "codes", icon: <QrcodeOutlined />, label: "二维码与流向" },
    { key: "codeNumbers", icon: <QrcodeOutlined />, label: "抽奖号码列表" },
  ] },
  { type: "group", label: "履约与风控", children: [
    { key: "records", icon: <GiftOutlined />, label: "中奖履约" },
    { key: "consumers", icon: <UserOutlined />, label: "用户档案与风控" },
  ] },
  { type: "group", label: "分析与系统", children: [
    { key: "analytics", icon: <LineChartOutlined />, label: "数据分析" },
    { key: "system", icon: <SettingOutlined />, label: "系统管理" },
  ] },
];
const statusColor = (value: string) =>
  value === "active" ? "success" : value === "frozen" ? "error" : "default";
const localDateTime = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";
const toApiDate = (value: string) => (value ? `${value}:00` : value);
const toInputDate = (value: string) => {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return Number.isNaN(date.getTime()) ? "" : new Date(date.getTime() - offset).toISOString().slice(0, 16);
};

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [loading, setLoading] = useState(false);
  const submit = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const result = await request<{ access_token: string }>(
        "/api/admin/auth/login",
        { method: "POST", body: JSON.stringify(values) },
      );
      setAdminToken(result.access_token);
      onSuccess();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };
  return (
    <main className="login-shell">
      <section className="login-brand">
        <div className="brand-mark">槟</div>
        <h1>槟郎营销运营台</h1>
        <p>一码一抽，活动全程可追溯</p>
      </section>
      <Card className="login-card" variant="borderless">
        <Typography.Title level={3}>登录运营台</Typography.Title>
        <Form
          layout="vertical"
          initialValues={{ username: "admin", password: "change-me-now" }}
          onFinish={submit}
        >
          <Form.Item name="username" label="账号" rules={[{ required: true }]}>
            <Input prefix={<UserOutlined />} />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={loading}
            block
            size="large"
          >
            登录
          </Button>
        </Form>
      </Card>
    </main>
  );
}

function AdminApp() {
  const [authed, setAuthed] = useState(
    Boolean(localStorage.getItem("betel-admin-token")),
  );
  const [page, setPage] = useState("overview");
  const [dealers, setDealers] = useState<Dealer[]>([]);
  const [dealerAccounts, setDealerAccounts] = useState<DealerAccount[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [accountDealer, setAccountDealer] = useState<Dealer>();
  const [batches, setBatches] = useState<Batch[]>([]);
  const [prizes, setPrizes] = useState<Prize[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [modal, setModal] = useState<
    | "dealer"
    | "store"
    | "dealerAccount"
    | "batch"
    | "prize"
    | "activity"
    | "editActivity"
    | "editPrize"
    | "distribution"
    | "pool"
    | "policy"
    | "adminUser"
    | null
  >(null);
  const [exporting, setExporting] = useState<number | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<number>();
  const [selectedActivityId, setSelectedActivityId] = useState<number>();
  const [codePage, setCodePage] = useState(1);
  const [codeStatus, setCodeStatus] = useState<string>();
  const [codeData, setCodeData] = useState<CodePage>({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  });
  const [configuration, setConfiguration] = useState<ActivityConfiguration>();
  const [winnerPage, setWinnerPage] = useState(1);
  const [recordKeyword, setRecordKeyword] = useState("");
  const [recordStatus, setRecordStatus] = useState<string>();
  const [winnerData, setWinnerData] = useState<WinnerPage>({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  });
  const [editingActivity, setEditingActivity] = useState<Activity>();
  const [editingPrize, setEditingPrize] = useState<Prize>();
  const [editingPolicy, setEditingPolicy] = useState<ActivityConfiguration["policies"][number]>();
  const [metrics, setMetrics] = useState<DashboardMetrics>();
  const [consumers, setConsumers] = useState<ConsumerSummary[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [adminAccounts, setAdminAccounts] = useState<AdminAccount[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [distributions, setDistributions] = useState<Distribution[]>([]);
  const selectedDealerAccount = accountDealer ? dealerAccounts.find((account) => account.dealer_id === accountDealer.id) : undefined;
  const refresh = async () => {
    try {
      const [d, b, p, a] = await Promise.all([
        request<Dealer[]>("/api/admin/dealers"),
        request<Batch[]>("/api/admin/qrcode/batches"),
        request<Prize[]>("/api/admin/prizes"),
        request<Activity[]>("/api/admin/activities"),
      ]);
      setDealers(d);
      setBatches(b);
      setPrizes(p);
      setActivities(a);
      setSelectedBatchId((current) => current ?? b[0]?.id);
      setSelectedActivityId((current) => current ?? a[0]?.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        localStorage.removeItem("betel-admin-token");
        setAuthed(false);
      } else {
        message.error(error instanceof Error ? error.message : "数据加载失败");
      }
    }
  };
  const refreshConfiguration = async (id = selectedActivityId) => {
    if (!id) {
      setConfiguration(undefined);
      return;
    }
    try {
      setConfiguration(
        await request<ActivityConfiguration>(
          `/api/admin/activities/${id}/configuration`,
        ),
      );
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "活动配置加载失败",
      );
    }
  };
  const refreshWinners = async () => {
    try {
      const query = new URLSearchParams({ page: String(winnerPage), page_size: "20" });
      if (recordKeyword.trim()) query.set("keyword", recordKeyword.trim());
      if (recordStatus) query.set("redemption_status", recordStatus);
      setWinnerData(
        await request<WinnerPage>(
          `/api/admin/lottery-records?${query.toString()}`,
        ),
      );
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "中奖记录加载失败",
      );
    }
  };
  const refreshDistributions = async () => {
    try {
      setDistributions(await request<Distribution[]>("/api/admin/qrcode/distributions"));
    } catch (error) {
      message.error(error instanceof Error ? error.message : "流向记录加载失败");
    }
  };
  const refreshCodeList = async (batchId = selectedBatchId, page = codePage, status = codeStatus) => {
    if (!batchId) return;
    try {
      setCodeData(await request<CodePage>(
        `/api/admin/qrcode/batches/${batchId}/codes?page=${page}&page_size=20${status ? `&code_status=${status}` : ""}`,
      ));
    } catch (error) {
      message.error(error instanceof Error ? error.message : "号码列表加载失败");
    }
  };
  const normalizeNewPrize = (values: Record<string, unknown>) => {
    const type = values.type as string;
    return {
      ...values,
      cash_amount: type === "cash" ? values.cash_amount : 0,
      upgrade_price: type === "upgrade" ? values.upgrade_price : 0,
      total_stock: values.total_stock,
      per_user_limit: values.per_user_limit,
    };
  };
  useEffect(() => {
    if (authed) void refresh();
  }, [authed]);
  useEffect(() => {
    if (!authed) return;
    if (page === "overview") {
      void request<DashboardMetrics>("/api/admin/overview").then(setMetrics).catch((error) => message.error(error instanceof Error ? error.message : "概览数据加载失败"));
    } else if (page === "dealers") {
      void Promise.all([
        request<Store[]>("/api/admin/stores"),
        request<DealerAccount[]>("/api/admin/dealer-accounts").catch(() => []),
      ]).then(([storeRows, accounts]) => { setStores(storeRows); setDealerAccounts(accounts); }).catch((error) => message.error(error instanceof Error ? error.message : "经销商数据加载失败"));
    } else if (page === "qrcodes") {
      void request<Distribution[]>("/api/admin/qrcode/distributions").then(setDistributions).catch((error) => message.error(error instanceof Error ? error.message : "流向记录加载失败"));
    } else if (page === "consumers") {
      void request<ConsumerSummary[]>("/api/admin/consumers").then(setConsumers).catch((error) => message.error(error instanceof Error ? error.message : "消费者数据加载失败"));
    }
  }, [authed, page]);
  useEffect(() => {
    if (!authed || !selectedBatchId) return;
    void refreshCodeList();
  }, [authed, selectedBatchId, codePage, codeStatus]);
  useEffect(() => {
    if (authed) void refreshConfiguration();
  }, [authed, selectedActivityId]);
  useEffect(() => {
    if (authed && page === "records") void refreshWinners();
  }, [authed, page, winnerPage, recordKeyword, recordStatus]);
  useEffect(() => {
    if (!authed || page !== "analytics") return;
    void request<TrendPoint[]>("/api/admin/analytics/trend?days=14").then(setTrend).catch((error) => message.error(error instanceof Error ? error.message : "趋势加载失败"));
  }, [authed, page]);
  useEffect(() => {
    if (!authed || page !== "system") return;
    void Promise.all([request<AdminAccount[]>("/api/admin/users"), request<{ items: AuditLog[] }>("/api/admin/audit-logs")]).then(([users, logs]) => { setAdminAccounts(users); setAuditLogs(logs.items); }).catch((error) => message.error(error instanceof Error ? error.message : "仅超级管理员可查看系统管理"));
  }, [authed, page]);
  const create = async (path: string, values: Record<string, unknown>, afterRefresh?: () => Promise<void>) => {
    await request(path, { method: "POST", body: JSON.stringify(values) });
    setModal(null);
    await refresh();
    if (afterRefresh) await afterRefresh();
    message.success("已保存");
  };
  const save = async (path: string, method: "PATCH" | "DELETE", values?: Record<string, unknown>, afterRefresh?: () => Promise<void>) => {
    await request(path, { method, ...(values ? { body: JSON.stringify(values) } : {}) });
    setModal(null);
    await refresh();
    if (afterRefresh) await afterRefresh();
    message.success(method === "DELETE" ? "已移除" : "已保存");
  };
  const body = useMemo(() => {
    if (page === "overview")
      return (
        <OverviewPage dealers={dealers} batches={batches} activities={activities} metrics={metrics} />
      );
    if (page === "dealers")
      return (
        <>
          <div className="page-heading">
            <div>
              <Typography.Title level={2}>经销商</Typography.Title>
              <Typography.Text type="secondary">
                流向记录仅供统计，不影响抽奖资格
              </Typography.Text>
            </div>
            <Button type="primary" onClick={() => setModal("dealer")}>
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
                      <Button size="small" type={account ? "default" : "primary"} onClick={() => { setAccountDealer(item); setModal("dealerAccount"); }}>{account ? "重置密码" : "设置密码"}</Button>
                      {item.status === "active" ? <Button size="small" danger onClick={() => Modal.confirm({ title: "冻结经销商", content: "冻结会立即停用其核销账号和门店核销权限。", okText: "确认冻结", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/dealers/${item.id}/freeze`, { method: "POST" }); await refresh(); message.success("经销商已冻结"); } })}>冻结</Button> : <><Button size="small" type="primary" onClick={() => Modal.confirm({ title: "恢复经销商", content: "将恢复经销商账号和门店核销权限。", okText: "确认恢复", onOk: async () => { await request(`/api/admin/dealers/${item.id}/unfreeze`, { method: "POST" }); await refresh(); message.success("经销商已恢复"); } })}>恢复</Button><Button size="small" danger onClick={() => Modal.confirm({ title: "删除冻结经销商", content: "仅无业务关联记录的经销商可删除。", okText: "确认删除", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/dealers/${item.id}`, { method: "DELETE" }); await refresh(); message.success("经销商已删除"); } })}>删除</Button></>}
                    </Space>;
                  },
                },
              ]}
            />
          </Card>
        </>
      );
    if (page === "stores")
      return <>
        <div className="page-heading">
          <div><Typography.Title level={2}>门店管理</Typography.Title><Typography.Text type="secondary">维护门店核销 PIN 与启停状态。停用后门店不能核销换购码。</Typography.Text></div>
          <Button type="primary" onClick={() => setModal("store")}>新建门店</Button>
        </div>
        <Card>
          <Table rowKey="id" dataSource={stores} locale={{ emptyText: "暂无门店" }} columns={[
            { title: "门店编号", dataIndex: "store_no" },
            { title: "门店名称", dataIndex: "name" },
            { title: "所属经销商", render: (_, item: Store) => dealers.find((dealer) => dealer.id === item.dealer_id)?.company_name || `#${item.dealer_id}` },
            { title: "联系人", render: (_, item: Store) => `${item.contact_name} / ${item.phone}` },
            { title: "状态", render: (_, item: Store) => <Tag color={item.status === "active" ? "success" : "default"}>{item.status === "active" ? "启用" : "停用"}</Tag> },
            { title: "操作", render: (_, item: Store) => <Space size={4}><Button size="small" onClick={() => { const redeemer_pin = window.prompt("请输入新的门店核销 PIN（至少 6 位）"); if (!redeemer_pin) return; if (redeemer_pin.length < 6) return message.error("PIN 至少 6 位"); void request(`/api/admin/stores/${item.id}/pin`, { method: "PATCH", body: JSON.stringify({ redeemer_pin }) }).then(() => message.success("门店 PIN 已重置")).catch((error) => message.error(error.message)); }}>重置 PIN</Button><Button size="small" danger={item.status === "active"} onClick={() => Modal.confirm({ title: item.status === "active" ? "停用门店" : "启用门店", content: item.status === "active" ? "停用后门店 PIN 不能继续核销换购码。" : "启用后门店可继续使用 PIN 核销换购码。", okText: "确认", onOk: async () => { await request(`/api/admin/stores/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ status: item.status === "active" ? "inactive" : "active" }) }); await refresh(); message.success("门店状态已更新"); } })}>{item.status === "active" ? "停用" : "启用"}</Button></Space> },
          ]} />
        </Card>
      </>;
    if (page === "consumers")
      return <><div className="page-heading"><div><Typography.Title level={2}>用户档案与风控</Typography.Title><Typography.Text type="secondary">查看参与记录，并可调整中奖系数或拦截异常用户。</Typography.Text></div></div><Card><Table rowKey="id" dataSource={consumers} locale={{ emptyText: "暂无用户" }} columns={[{ title: "微信用户", render: (_, item: ConsumerSummary) => <><b>{item.nickname}</b><br /><Typography.Text type="secondary">OpenID：{item.openid}</Typography.Text></> }, { title: "参与 / 中奖", render: (_, item: ConsumerSummary) => `${item.draw_count} / ${item.win_count} 次` }, { title: "中奖系数", render: (_, item: ConsumerSummary) => `${(Number(item.win_probability_multiplier) * 100).toFixed(2)}%` }, { title: "状态", render: (_, item: ConsumerSummary) => <><Tag color={item.risk_status === "blocked" ? "error" : "success"}>{item.risk_status === "blocked" ? "已拦截" : "正常"}</Tag>{item.risk_note && <Typography.Text type="secondary"> {item.risk_note}</Typography.Text>}</> }, { title: "最近抽奖", dataIndex: "last_draw_at", render: localDateTime }, { title: "注册时间", dataIndex: "created_at", render: localDateTime }, { title: "操作", render: (_, item: ConsumerSummary) => <Space size={4}><Button size="small" onClick={() => void request<{ records: { id: number; code_no: string; activity_name: string; prize_name: string; client_ip?: string | null; client_location?: string | null; client_province?: string | null; client_city?: string | null; client_district?: string | null; client_address?: string | null; created_at: string }[] }>(`/api/admin/consumers/${item.id}`).then(detail => Modal.info({ title: `${item.nickname} 的用户详情`, width: 860, content: <Table size="small" pagination={false} rowKey="id" dataSource={detail.records} columns={[{ title: "抽奖号码", dataIndex: "code_no" }, { title: "活动", dataIndex: "activity_name" }, { title: "奖品", dataIndex: "prize_name" }, { title: "IP / 解析地址", render: (_, row) => <>{row.client_ip || "未采集"}<br /><Typography.Text type="secondary">{[row.client_province, row.client_city, row.client_district].filter(Boolean).join(" ") || row.client_address || row.client_location || "未授权"}</Typography.Text></> }, { title: "时间", dataIndex: "created_at", render: localDateTime }]} /> })).catch(error => message.error(error.message))}>用户详情</Button><Button size="small" onClick={() => { setRecordKeyword(item.openid); setWinnerPage(1); setPage("records"); }}>查看记录</Button><Button size="small" onClick={() => { const value = window.prompt("中奖系数（0-1，例如 0.5 表示按奖池概率的 50% 中奖）", item.win_probability_multiplier); if (value === null) return; const multiplier = Number(value); if (!Number.isFinite(multiplier) || multiplier < 0 || multiplier > 1) return message.error("请输入 0 到 1 之间的数值"); void request(`/api/admin/consumers/${item.id}/win-probability`, { method: "PATCH", body: JSON.stringify({ win_probability_multiplier: multiplier }) }).then(async () => { await refresh(); message.success("中奖系数已更新"); }).catch(error => message.error(error.message)); }}>中奖系数</Button><Button size="small" danger={item.risk_status !== "blocked"} onClick={() => Modal.confirm({ title: item.risk_status === "blocked" ? "恢复用户" : "拦截用户", content: item.risk_status === "blocked" ? "恢复后该用户可继续参与活动。" : "拦截后该用户不可扫码和开奖。", onOk: async () => { await request(`/api/admin/consumers/${item.id}/risk`, { method: "PATCH", body: JSON.stringify({ risk_status: item.risk_status === "blocked" ? "normal" : "blocked", risk_note: item.risk_status === "blocked" ? "" : "后台人工拦截" }) }); await refresh(); message.success("用户状态已更新"); } })}>{item.risk_status === "blocked" ? "恢复" : "拦截"}</Button>{!item.anonymized_at && <Button size="small" danger onClick={() => Modal.confirm({ title: "匿名化用户数据", content: "将清除该用户的 OpenID、昵称、IP 与定位信息，且不可恢复。存在待兑奖奖品时会被拒绝。", okText: "确认匿名化", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/consumers/${item.id}/anonymize`, { method: "POST" }); await refresh(); message.success("用户数据已匿名化"); } })}>匿名化</Button>}</Space> }]} /></Card></>;
    if (page === "codes")
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
          <Card className="distribution-card" title="流向登记记录" extra={<Space><Button onClick={() => void refreshDistributions()}>刷新记录</Button><Button type="primary" onClick={() => setModal("distribution")}>登记流向</Button></Space>}>
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
          <Card className="code-list-card" title="二维码批次" extra={<Button type="primary" onClick={() => setModal("batch")}>创建抽奖批次</Button>}>
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
                        onClick={() => {
                          setExporting(item.id);
                          void download(
                            `/api/admin/qrcode/batches/${item.id}/export.csv`,
                            `${item.batch_no}-codes.csv`,
                          )
                            .catch((error) => message.error(error.message))
                            .finally(() => setExporting(null));
                        }}
                      >
                        印刷清单 CSV
                      </Button>
                      <Button
                        icon={<DownloadOutlined />}
                        size="small"
                        loading={exporting === item.id}
                        onClick={() => {
                          setExporting(item.id);
                          void download(
                            `/api/admin/qrcode/batches/${item.id}/export`,
                            `${item.batch_no}-print-package.zip`,
                          )
                            .catch((error) => message.error(error.message))
                            .finally(() => setExporting(null));
                        }}
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
                        onClick={() => {
                          setSelectedBatchId(item.id);
                          setCodePage(1);
                          setPage("codeNumbers");
                        }}
                      >
                        查看号码
                      </Button>
                      <Button
                        size="small"
                        type={item.activation_status === "active" ? "default" : "primary"}
                        disabled={item.activation_status === "active"}
                        onClick={() => Modal.confirm({
                          title: "激活二维码批次",
                          content: item.can_activate ? `确认激活批次 ${item.batch_no} 中全部待激活的二维码？` : item.activation_block_reason || "当前批次不可激活",
                          okText: item.can_activate ? "确认激活" : "去创建活动",
                          cancelText: "取消",
                          onOk: async () => {
                            if (!item.can_activate) {
                              setPage("activities");
                              return;
                            }
                            try {
                              const result = await request<{ activated_count: number }>(`/api/admin/qrcode/batches/${item.id}/activate`, { method: "POST", body: JSON.stringify({}) });
                              await Promise.all([refresh(), refreshCodeList(item.id, item.id === selectedBatchId ? codePage : 1)]);
                              message.success(result.activated_count ? `已激活 ${result.activated_count} 个二维码` : "没有待激活的二维码");
                            } catch (error) {
                              message.error(error instanceof Error ? error.message : "批次激活失败");
                            }
                          },
                        })}
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
    if (page === "codeNumbers")
      return (
        <>
          <div className="page-heading">
            <div>
              <Typography.Title level={2}>抽奖号码列表</Typography.Title>
              <Typography.Text type="secondary">袋外二维码与袋内兑奖码一一对应。列表仅显示是否已配置兑奖码，明文仅在印刷导出文件中提供。</Typography.Text>
            </div>
          </div>
          <Card
            className="code-list-card"
            title="抽奖号码列表"
            extra={
              <Space><Select value={selectedBatchId} style={{ width: 240 }} options={batches.map((item) => ({ value: item.id, label: `${item.batch_no} · ${item.batch_name}` }))} onChange={(value) => { setSelectedBatchId(value); setCodePage(1); }} /><Select allowClear placeholder="全部状态" value={codeStatus} style={{ width: 120 }} options={[{ value: "inactive", label: "待激活" }, { value: "active", label: "可使用" }, { value: "drawn", label: "已开奖" }, { value: "void", label: "已作废" }]} onChange={(value) => { setCodeStatus(value); setCodePage(1); }} /><Button aria-label="刷新抽奖号码列表" title="刷新抽奖号码列表" icon={<ReloadOutlined />} onClick={() => void refreshCodeList().then(() => message.success("抽奖号码列表已刷新"))} /></Space>
            }
          >
            <Table
              size="small"
              pagination={false}
              rowKey="id"
              dataSource={codeData.items}
              columns={[
                { title: "抽奖号码", dataIndex: "code_no" },
                { title: "袋内兑奖码", dataIndex: "has_redemption_code", render: (value?: boolean) => <Tag color={value ? "success" : "warning"}>{value ? "已配置 4 位码" : "旧批次无兑奖码"}</Tag> },
                { title: "状态", dataIndex: "status", render: (value: string) => <Tag color={value === "active" ? "success" : value === "drawn" ? "blue" : value === "void" ? "error" : "default"}>{value === "active" ? "可使用" : value === "drawn" ? "已开奖" : value === "void" ? "已作废" : "待激活"}</Tag> },
                {
                  title: "生成时间",
                  dataIndex: "created_at",
                  render: localDateTime,
                },
                {
                  title: "抽奖时间",
                  dataIndex: "drawn_at",
                  render: localDateTime,
                },
                { title: "操作", render: (_, item: LotteryCode) => <Space size={4}>{item.status === "inactive" && <Button type="primary" size="small" onClick={() => Modal.confirm({ title: "激活抽奖号码", content: `确认激活 ${item.code_no}？`, okText: "确认激活", onOk: async () => { const result = await request<{ activated_count: number }>(`/api/admin/qrcode/batches/${selectedBatchId}/activate-selected`, { method: "POST", body: JSON.stringify({ code_ids: [item.id] }) }); await Promise.all([refreshCodeList(), refresh()]); message.success(result.activated_count ? "号码已激活" : "该号码无需激活"); } })}>激活</Button>}{item.status !== "drawn" && item.status !== "void" && <Button danger size="small" onClick={() => Modal.confirm({ title: "作废抽奖号码", content: `确认作废 ${item.code_no}？作废后无法恢复，也不能参与抽奖。`, okText: "确认作废", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/qrcode/${item.id}/void`, { method: "PATCH" }); await Promise.all([refreshCodeList(), refresh()]); message.success("号码已作废"); } })}>作废</Button>}</Space> },
              ]}
            />
            <div className="code-pagination">
              <Typography.Text type="secondary">
                共 {codeData.total} 个号码
              </Typography.Text>
              <Pagination
                current={codePage}
                pageSize={20}
                total={codeData.total}
                showSizeChanger={false}
                onChange={setCodePage}
              />
            </div>
          </Card>
        </>
      );
    if (page === "records")
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
              <Input.Search allowClear placeholder="姓名、抽奖号、兑换码或奖品" style={{ width: 240 }} onSearch={(value) => { setWinnerPage(1); setRecordKeyword(value); }} />
              <Select allowClear placeholder="兑换状态" style={{ width: 130 }} options={[{ value: "pending", label: "待兑换" }, { value: "redeemed", label: "已兑换" }, { value: "expired", label: "已过期" }, { value: "not_required", label: "无需兑换" }]} onChange={(value) => { setWinnerPage(1); setRecordStatus(value); }} />
              <Button icon={<DownloadOutlined />} onClick={() => { const query = new URLSearchParams(); if (recordKeyword.trim()) query.set("keyword", recordKeyword.trim()); if (recordStatus) query.set("redemption_status", recordStatus); void download(`/api/admin/lottery-records/export.csv?${query.toString()}`, "中奖记录.csv").catch(error => message.error(error.message)); }}>导出</Button>
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
                onChange={setWinnerPage}
              />
            </div>
          </Card>
        </>
      );
    if (page === "analytics") return <><div className="page-heading"><div><Typography.Title level={2}>数据分析</Typography.Title><Typography.Text type="secondary">近 14 天扫码核验、开奖、中奖和现金发放趋势。</Typography.Text></div></div><Card title="每日运营数据"><Table rowKey="date" pagination={false} dataSource={trend} columns={[{ title: "日期", dataIndex: "date" }, { title: "扫码核验", dataIndex: "scan_count" }, { title: "抽奖次数", dataIndex: "draw_count" }, { title: "中奖次数", dataIndex: "winner_count" }, { title: "现金发放", dataIndex: "cash_amount", render: (value) => `¥${Number(value).toFixed(2)}` }]} /></Card></>;
    if (page === "system") return <><div className="page-heading"><div><Typography.Title level={2}>系统管理</Typography.Title><Typography.Text type="secondary">账号启停和关键运营操作均留存审计记录。</Typography.Text></div><Button type="primary" onClick={() => setModal("adminUser")}>新建运营账号</Button></div><section className="dashboard-grid"><Card title="后台账号"><Table size="small" rowKey="id" pagination={false} dataSource={adminAccounts} columns={[{ title: "账号", dataIndex: "username" }, { title: "角色", dataIndex: "role", render: (value) => value === "super_admin" ? "超级管理员" : "运营员" }, { title: "状态", render: (_, item: AdminAccount) => <Tag color={item.is_active ? "success" : "default"}>{item.is_active ? "启用" : "停用"}</Tag> }, { title: "操作", render: (_, item: AdminAccount) => <Button size="small" disabled={item.username === "admin" && item.is_active} onClick={() => void request(`/api/admin/users/${item.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !item.is_active }) }).then(() => { message.success("账号状态已更新"); setPage("overview"); setTimeout(() => setPage("system"), 0); }).catch(error => message.error(error.message))}>{item.is_active ? "停用" : "启用"}</Button> }]} /></Card><Card title="最近审计日志"><Table size="small" rowKey="id" pagination={false} dataSource={auditLogs} columns={[{ title: "时间", dataIndex: "created_at", render: localDateTime }, { title: "操作", dataIndex: "action" }, { title: "对象", render: (_, item: AuditLog) => `${item.target_type} #${item.target_id ?? "-"}` }, { title: "详情", dataIndex: "detail" }]} /></Card></section></>;
    if (page === "prizes") return <>
      <div className="page-heading">
        <div><Typography.Title level={2}>奖品库</Typography.Title><Typography.Text type="secondary">维护库存、奖励类型与发放状态。停用奖品后不会再进入任何奖池候选。</Typography.Text></div>
        <Button type="primary" onClick={() => setModal("prize")}>新建奖品</Button>
      </div>
      <Card>
        <Table
          rowKey="id"
          dataSource={prizes}
          columns={[
            { title: "奖品 / 换购产品", dataIndex: "name" },
            { title: "类型", dataIndex: "type", render: (value: string) => ({ cash: "现金", upgrade: "加价换购" }[value as "cash" | "upgrade"] ?? value) },
            { title: "换购加价", render: (_, item: Prize) => item.type === "upgrade" ? `¥${Number(item.upgrade_price || 0).toFixed(2)}` : "-" },
            { title: "库存", render: (_, item: Prize) => `${item.issued_stock}/${item.total_stock}` },
            { title: "状态", dataIndex: "status", render: (value: string) => <Tag color={value === "active" ? "success" : "default"}>{value === "active" ? "启用" : "停用"}</Tag> },
            { title: "操作", render: (_, item: Prize) => <Space size={4}>
              <Button size="small" onClick={() => { setEditingPrize(item); setModal("editPrize"); }}>编辑</Button>
              <Button size="small" danger={item.status === "active"} type={item.status === "active" ? "default" : "primary"} onClick={() => void request(`/api/admin/prizes/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ status: item.status === "active" ? "inactive" : "active" }) }).then(async () => { await refresh(); await refreshConfiguration(); message.success(item.status === "active" ? "奖品已停用" : "奖品已启用"); }).catch((error) => message.error(error.message))}>{item.status === "active" ? "停用" : "启用"}</Button>
              {item.status !== "active" && <Button size="small" danger onClick={() => Modal.confirm({ title: "删除停用奖品", content: "已被奖池、策略、开奖记录、券码或履约数据引用的奖品不能删除。", okText: "确认删除", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/prizes/${item.id}`, { method: "DELETE" }); await refresh(); await refreshConfiguration(); message.success("奖品已删除"); } })}>删除</Button>}
            </Space> },
          ]}
        />
      </Card>
    </>;
    if (page === "policies") return <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>区域策略</Typography.Title>
          <Typography.Text type="secondary">为经销商、省、市或区县发布独立奖池。发布后自动生成新版本，旧版本保留用于审计。</Typography.Text>
        </div>
        <Space>
          <Select value={selectedActivityId} placeholder="选择活动" style={{ width: 240 }} options={activities.map((item) => ({ value: item.id, label: item.name }))} onChange={setSelectedActivityId} />
          <Button type="primary" disabled={!selectedActivityId} onClick={() => setModal("policy")}>发布策略</Button>
        </Space>
      </div>
      <div className="config-summary strategy-summary">
        <span>当前活动：{activities.find((item) => item.id === selectedActivityId)?.name ?? "未选择"}</span>
        <span>生效策略：{configuration?.policies.filter((item) => item.status === "active").length ?? 0} 条</span>
        <span>策略优先级：经销商 &gt; 区县 &gt; 城市 &gt; 省份 &gt; 默认奖池</span>
      </div>
      <Card className="strategy-table" title="已发布策略">
        <Table
          rowKey="id"
          pagination={false}
          dataSource={configuration?.policies ?? []}
          locale={{ emptyText: selectedActivityId ? "尚未发布区域策略，当前将使用活动默认奖池" : "请先选择活动" }}
          columns={[
            { title: "适用范围", render: (_, item: ActivityConfiguration["policies"][number]) => item.scope === "dealer" ? `经销商 #${item.dealer_id}` : [item.province, item.city, item.district].filter(Boolean).join(" / ") },
            { title: "版本", dataIndex: "version", width: 88 },
            { title: "状态", dataIndex: "status", width: 100, render: (value) => <Tag color={value === "active" ? "success" : "default"}>{value === "active" ? "生效中" : value === "inactive" ? "已停用" : "已替换"}</Tag> },
            { title: "奖池配置", render: (_, item: ActivityConfiguration["policies"][number]) => item.prizes.map((prize) => `${prize.prize_name} ${(Number(prize.probability) * 100).toFixed(2)}%`).join("；") },
            { title: "发布备注", dataIndex: "note" },
            { title: "操作", width: 220, render: (_, item: ActivityConfiguration["policies"][number]) => item.status === "active" ? <Space size={4}><Button size="small" onClick={() => { setEditingPolicy(item); setModal("policy"); }}>发布新版本</Button><Button size="small" danger onClick={() => Modal.confirm({ title: "停用区域策略", content: "停用后该范围将回退到更低优先级策略或活动默认奖池。", okText: "确认停用", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/policies/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ status: "inactive" }) }); await refreshConfiguration(); message.success("策略已停用"); } })}>停用</Button></Space> : <Typography.Text type="secondary">审计留存</Typography.Text> },
          ]}
        />
      </Card>
    </>;
    const selectedActivity = activities.find(
      (item) => item.id === selectedActivityId,
    );
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
            <Button onClick={() => setPage("prizes")}>管理奖品</Button>
            <Button type="primary" onClick={() => setModal("activity")}>
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
                onClick: () => setSelectedActivityId(item.id),
              })}
              columns={[
                { title: "活动名称", dataIndex: "name" },
                {
                  title: "开始时间",
                  dataIndex: "start_at",
                  render: localDateTime,
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
                      <Button size="small" onClick={() => { setEditingActivity(item); setModal("editActivity"); }}>编辑</Button>
                      <Button size="small" danger={item.status === "active"} type={item.status === "active" ? "default" : "primary"} onClick={() => void request(`/api/admin/activities/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ status: item.status === "active" ? "draft" : "active" }) }).then(async () => { await refresh(); await refreshConfiguration(); message.success(item.status === "active" ? "活动已停用" : "活动已启用"); }).catch((error) => message.error(error.message))}>{item.status === "active" ? "停用" : "启用"}</Button>
                      {item.status !== "active" && <Button size="small" danger onClick={() => Modal.confirm({ title: "删除停用活动", content: "活动没有开奖记录时，删除会同时清理默认奖池、批次关联和地区策略。", okText: "确认删除", okButtonProps: { danger: true }, onOk: async () => { await request(`/api/admin/activities/${item.id}`, { method: "DELETE" }); await refresh(); await refreshConfiguration(); message.success("活动已删除"); } })}>删除</Button>}
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
                onChange={setSelectedActivityId}
              />
              <Button
                disabled={!selectedActivityId}
                type="primary"
                onClick={() => setModal("pool")}
              >
                加入奖池
              </Button>
              <Button disabled={!selectedActivityId} onClick={() => setPage("policies")}>
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
            <Select value={selectedBatchId} placeholder="选择要关联的批次" style={{ width: 240 }} options={batches.filter((batch) => !configuration?.batch_ids.includes(batch.id)).map((batch) => ({ value: batch.id, label: `${batch.batch_no} · ${batch.batch_name}` }))} onChange={setSelectedBatchId} />
            <Button disabled={!selectedBatchId} onClick={() => void request(`/api/admin/activities/${selectedActivityId}/batches/${selectedBatchId}`, { method: "POST" }).then(() => refreshConfiguration()).then(() => message.success("批次已关联")).catch((error) => message.error(error instanceof Error ? error.message : "关联失败"))}>关联批次</Button>
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
                    <Button size="small" onClick={() => {
                      const probability = window.prompt("中奖概率（0-1）", item.probability);
                      const dailyLimit = window.prompt("每日发放上限（0 表示不限）", String(item.daily_limit));
                      if (probability === null || dailyLimit === null || !selectedActivityId) return;
                      void save(`/api/admin/activities/${selectedActivityId}/prizes/${item.id}`, "PATCH", { probability: Number(probability), daily_limit: Number(dailyLimit) }).catch(error => message.error(error instanceof Error ? error.message : "更新失败"));
                    }}>编辑</Button>
                    <Button danger size="small" onClick={() => { if (selectedActivityId && window.confirm(`移除奖池项“${item.prize_name}”？`)) void save(`/api/admin/activities/${selectedActivityId}/prizes/${item.id}`, "DELETE").catch(error => message.error(error instanceof Error ? error.message : "移除失败")); }}>移除</Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      </>
    );
  }, [
    page,
    dealers,
    consumers,
    batches,
    prizes,
    activities,
    exporting,
    selectedBatchId,
    selectedActivityId,
    codePage,
    codeData,
    configuration,
    winnerData,
    winnerPage,
    recordKeyword,
    recordStatus,
    metrics,
  ]);
  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;
  return (
    <Layout className="shell">
      <Layout.Sider width={236} className="sider">
        <div className="side-brand">
          <span>槟</span>
          <b>槟郎运营台</b>
        </div>
        <Menu
          theme="dark"
          selectedKeys={[page]}
          items={nav}
          onClick={({ key }) => setPage(key)}
        />
        <div className="side-footer">
          管理员
          <br />
          <small>超级管理员</small>
        </div>
      </Layout.Sider>
      <Layout>
        <Layout.Header className="header">
          <Typography.Text>
            营销活动 / <b>{pageLabels[page] ?? "经营概览"}</b>
          </Typography.Text>
          <Button
            type="text"
            onClick={() => {
              localStorage.removeItem("betel-admin-token");
              setAuthed(false);
            }}
          >
            退出
          </Button>
        </Layout.Header>
        <Layout.Content className="content">{body}</Layout.Content>
      </Layout>
      <Modal
        open={modal === "dealerAccount"}
        title={selectedDealerAccount ? "重置经销商密码" : "设置经销商账号密码"}
        footer={null}
        onCancel={() => { setModal(null); setAccountDealer(undefined); }}
      >
        <Form
          key={accountDealer?.id}
          layout="vertical"
          initialValues={{ username: accountDealer?.phone }}
          onFinish={(values) => {
            if (!accountDealer) return;
            const requestPromise = selectedDealerAccount
              ? request(`/api/admin/dealer-accounts/${selectedDealerAccount.id}/password`, { method: "PATCH", body: JSON.stringify({ password: values.password }) })
              : request("/api/admin/dealer-accounts", { method: "POST", body: JSON.stringify({ dealer_id: accountDealer.id, password: values.password }) });
            void requestPromise.then(async () => { setModal(null); setAccountDealer(undefined); if (page === "dealers") await request<DealerAccount[]>("/api/admin/dealer-accounts").then(setDealerAccounts).catch(() => {}); message.success(selectedDealerAccount ? "经销商密码已重置" : "经销商账号已创建"); }).catch((error) => message.error(error.message));
          }}
        >
          <Typography.Text type="secondary">经销商：{accountDealer?.company_name}</Typography.Text>
          {selectedDealerAccount ? <Form.Item label="登录账号"><Input value={selectedDealerAccount.username} disabled /></Form.Item> : <Form.Item name="username" label="登录账号"><Input disabled /></Form.Item>}
          <Form.Item name="password" label={selectedDealerAccount ? "新密码" : "初始密码"} rules={[{ required: true }, { min: 8, message: "至少 8 个字符" }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Typography.Text type="secondary">登录账号固定为经销商手机号；密码至少 8 个字符。设置后请通过安全渠道告知经销商。</Typography.Text>
          <Button htmlType="submit" type="primary" block style={{ marginTop: 16 }}>{selectedDealerAccount ? "确认重置" : "创建账号并设置密码"}</Button>
        </Form>
      </Modal>
      <Modal
        open={modal === "dealer"}
        title="新建经销商"
        footer={null}
        onCancel={() => setModal(null)}
      >
        <Form
          layout="vertical"
          onFinish={(values) => void create("/api/admin/dealers", values)}
        >
          <Form.Item
            name="company_name"
            label="公司名称"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <div className="two-col">
            <Form.Item
              name="contact_name"
              label="联系人"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="phone" label="手机号" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
          </div>
          <div className="two-col">
            <Form.Item
              name="province"
              label="省份"
              initialValue="湖南"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="city"
              label="城市"
              initialValue="长沙"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
          </div>
          <Form.Item name="level" label="等级" initialValue={1}>
            <Select
              options={[1, 2, 3].map((value) => ({
                value,
                label: `L${value}`,
              }))}
            />
          </Form.Item>
          <Button htmlType="submit" type="primary" block>
            保存
          </Button>
        </Form>
      </Modal>
      <Modal open={modal === "store"} title="新建门店" footer={null} onCancel={() => setModal(null)}>
        <Form layout="vertical" onFinish={(values) => void create("/api/admin/stores", values).catch((error) => message.error(error instanceof Error ? error.message : "创建失败"))}>
          <Form.Item name="store_no" label="门店编号" rules={[{ required: true }, { min: 3, message: "至少 3 个字符" }]}><Input autoComplete="off" /></Form.Item>
          <Form.Item name="name" label="门店名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="dealer_id" label="所属经销商" rules={[{ required: true }]}><Select options={dealers.filter((item) => item.status === "active").map((item) => ({ value: item.id, label: `${item.dealer_no} · ${item.company_name}` }))} /></Form.Item>
          <div className="two-col"><Form.Item name="contact_name" label="联系人" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="phone" label="联系电话" rules={[{ required: true }]}><Input /></Form.Item></div>
          <Form.Item name="redeemer_pin" label="门店核销 PIN" rules={[{ required: true }, { min: 6, message: "至少 6 位" }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Button htmlType="submit" type="primary" block>创建门店</Button>
        </Form>
      </Modal>
      <Modal
        open={modal === "batch"}
        title="创建抽奖批次"
        footer={null}
        onCancel={() => setModal(null)}
      >
        <Form
          layout="vertical"
          onFinish={(values) =>
            void create("/api/admin/qrcode/batches", values)
          }
        >
          <Form.Item
            name="batch_no"
            label="批次号"
            rules={[{ required: true }]}
          >
            <Input placeholder="202608-HN-01" />
          </Form.Item>
          <Form.Item
            name="batch_name"
            label="批次名称"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <div className="two-col">
            <Form.Item
              name="prefix"
              label="码前缀"
              rules={[{ required: true }]}
            >
              <Input placeholder="BLHN" />
            </Form.Item>
            <Form.Item
              name="total_count"
              label="生成数量"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={100000} style={{ width: "100%" }} />
            </Form.Item>
          </div>
          <Button htmlType="submit" type="primary" block>
            生成袋外二维码与袋内兑奖码
          </Button>
        </Form>
      </Modal>
      <Modal open={modal === "distribution"} title="登记二维码流向" footer={null} onCancel={() => setModal(null)}>
        <Form layout="vertical" onFinish={(values) => void request("/api/admin/qrcode/distributions", { method: "POST", body: JSON.stringify(values) }).then(async () => { setModal(null); await refresh(); await refreshDistributions(); message.success("流向记录已登记"); }).catch(error => message.error(error instanceof Error ? error.message : "登记失败"))}>
          <Form.Item name="batch_id" label="二维码批次" rules={[{ required: true }]}><Select options={batches.map(item => ({ value: item.id, label: `${item.batch_no} · ${item.batch_name}` }))} /></Form.Item>
          <Form.Item name="dealer_id" label="流向经销商" rules={[{ required: true }]}><Select options={dealers.filter(item => item.status === "active").map(item => ({ value: item.id, label: `${item.dealer_no} · ${item.company_name}` }))} /></Form.Item>
          <div className="two-col"><Form.Item name="start_code_no" label="起始印刷号码（可选）"><Input placeholder="留空则登记整个批次" /></Form.Item><Form.Item name="end_code_no" label="结束印刷号码（可选）"><Input placeholder="留空则登记整个批次" /></Form.Item></div>
          <Typography.Text type="secondary">不填号码时，默认将该批次全部号码登记给经销商；如需拆分批次，请同时填写起始和结束印刷号码。号码段必须连续且未分配给其他经销商。</Typography.Text>
          <Button htmlType="submit" type="primary" block style={{ marginTop: 16 }}>保存流向记录</Button>
        </Form>
      </Modal>
      <Modal
        open={modal === "prize"}
        title="新建奖品"
        footer={null}
        onCancel={() => setModal(null)}
      >
        <Form
          layout="vertical"
          initialValues={{
            type: "cash",
            cash_amount: 1,
            total_stock: 100,
            per_user_limit: 1,
          }}
          onFinish={(values) => void create("/api/admin/prizes", normalizeNewPrize(values)).catch((error) => Modal.error({ title: "创建奖品失败", content: error instanceof Error ? error.message : "请求失败" }))}
        >
          <Typography.Text strong>奖品信息</Typography.Text>
          <div className="two-col prize-basic-fields">
            <Form.Item name="name" label="奖品名称" rules={[{ required: true }]}>
              <Input placeholder="例如：1 元现金红包" />
            </Form.Item>
            <Form.Item name="type" label="奖品类型" rules={[{ required: true }]}>
              <Select options={[
                { value: "cash", label: "现金" },
                { value: "upgrade", label: "加价换购" },
              ]} />
            </Form.Item>
          </div>
          <Form.Item noStyle shouldUpdate={(previous, current) => previous.type !== current.type}>
            {({ getFieldValue }) => {
              const type = getFieldValue("type");
              const needsStock = true;
              return <>
                {type === "cash" && <><Divider className="prize-form-divider" /><Typography.Text strong>奖项设置</Typography.Text><Form.Item className="prize-setting-field" name="cash_amount" label="红包金额（元）" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></>}
                {type === "upgrade" && <><Divider className="prize-form-divider" /><Typography.Text strong>换购产品与价格</Typography.Text><Typography.Paragraph type="secondary">奖品名称就是用户和经销商看到的换购产品名称。每个不同产品或不同加价档位都应分别创建一条奖品。</Typography.Paragraph><Form.Item className="prize-setting-field" name="upgrade_price" label="用户补差价（元）" rules={[{ required: true }]}><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item></>}
                {needsStock && <><Divider className="prize-form-divider" /><Typography.Text strong>库存与限领</Typography.Text><div className="two-col prize-stock-fields"><Form.Item name="total_stock" label="总库存" rules={[{ required: true }]}><InputNumber min={0} style={{ width: "100%" }} /></Form.Item><Form.Item name="per_user_limit" label="单用户限领" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item></div></>}
              </>;
            }}
          </Form.Item>
          <Button htmlType="submit" type="primary" block>
            保存奖品
          </Button>
        </Form>
      </Modal>
      <Modal
        open={modal === "activity"}
        title="创建活动"
        footer={null}
        onCancel={() => setModal(null)}
      >
        <Form
          layout="vertical"
          initialValues={{ status: "draft" }}
          onFinish={(values) =>
            void create("/api/admin/activities", {
              ...values,
              start_at: toApiDate(values.start_at),
              end_at: toApiDate(values.end_at),
            })
          }
        >
          <Form.Item name="name" label="活动名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="活动说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="rules" label="活动规则（每行一条）">
            <Input.TextArea rows={3} />
          </Form.Item>
          <div className="two-col">
            <Form.Item
              name="start_at"
              label="开始时间"
              rules={[{ required: true }]}
            >
              <Input type="datetime-local" />
            </Form.Item>
            <Form.Item
              name="end_at"
              label="结束时间"
              rules={[{ required: true }]}
            >
              <Input type="datetime-local" />
            </Form.Item>
          </div>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: "draft", label: "草稿" },
                { value: "active", label: "立即启用" },
              ]}
            />
          </Form.Item>
          <Button htmlType="submit" type="primary" block>
            保存活动
          </Button>
        </Form>
      </Modal>
      <Modal open={modal === "editActivity"} title="编辑活动" footer={null} onCancel={() => setModal(null)}>
        <Form key={editingActivity?.id} layout="vertical" initialValues={{ ...editingActivity, all_batches: editingActivity?.all_batches ?? true, start_at: editingActivity ? toInputDate(editingActivity.start_at) : "", end_at: editingActivity ? toInputDate(editingActivity.end_at) : "" }} onFinish={(values) => { if (editingActivity) void save(`/api/admin/activities/${editingActivity.id}`, "PATCH", { ...values, start_at: toApiDate(values.start_at), end_at: toApiDate(values.end_at) }).catch(error => message.error(error instanceof Error ? error.message : "保存失败")); }}>
          <Form.Item name="name" label="活动名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="后台备注"><Input.TextArea rows={2} /></Form.Item>
          <div className="two-col"><Form.Item name="start_at" label="开始时间" rules={[{ required: true }]}><Input type="datetime-local" /></Form.Item><Form.Item name="end_at" label="结束时间" rules={[{ required: true }]}><Input type="datetime-local" /></Form.Item></div>
          <Form.Item name="status" label="活动状态"><Select options={[{ value: "draft", label: "草稿（禁止参与）" }, { value: "active", label: "启用" }, { value: "ended", label: "结束（禁止参与）" }]} /></Form.Item>
          <Form.Item name="all_batches" valuePropName="checked"><Checkbox>适用于所有已激活批次</Checkbox></Form.Item>
          <Form.Item name="rules" label="活动规则（每行一条）"><Input.TextArea rows={3} /></Form.Item>
          <Button htmlType="submit" type="primary" block>保存活动</Button>
        </Form>
      </Modal>
      <Modal open={modal === "editPrize"} title="编辑奖品" footer={null} onCancel={() => setModal(null)}>
        <Form key={editingPrize?.id} layout="vertical" initialValues={editingPrize} onFinish={(values) => { if (editingPrize) void save(`/api/admin/prizes/${editingPrize.id}`, "PATCH", values).catch(error => message.error(error instanceof Error ? error.message : "保存失败")); }}>
          <Form.Item name="name" label={editingPrize?.type === "upgrade" ? "换购产品名称" : "奖品名称"} rules={[{ required: true }]}><Input /></Form.Item>
          {editingPrize?.type === "cash" ? <Form.Item name="cash_amount" label="微信红包金额（元)"><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item> : <Form.Item name="upgrade_price" label="用户补差价（元）"><InputNumber min={0.01} precision={2} style={{ width: "100%" }} /></Form.Item>}<Form.Item name="total_stock" label="总库存"><InputNumber min={0} precision={0} style={{ width: "100%" }} /></Form.Item>
          <div className="two-col"><Form.Item name="per_user_limit" label="单用户上限"><InputNumber min={1} precision={0} style={{ width: "100%" }} /></Form.Item><Form.Item name="status" label="状态"><Select options={[{ value: "active", label: "启用" }, { value: "inactive", label: "停用" }]} /></Form.Item></div>
          <Form.Item name="type" hidden><Input /></Form.Item>
          <Button htmlType="submit" type="primary" block>保存奖品</Button>
        </Form>
      </Modal>
      <Modal
        open={modal === "pool"}
        title="加入活动奖池"
        footer={null}
        onCancel={() => setModal(null)}
      >
        <Form
          layout="vertical"
          initialValues={{ probability: 0.1, daily_limit: 0 }}
          onFinish={(values) => {
            if (selectedActivityId)
              void create(
                `/api/admin/activities/${selectedActivityId}/prizes`,
                values,
              );
          }}
        >
          <Form.Item
            name="prize_id"
            label="选择奖品"
            rules={[{ required: true }]}
          >
            <Select
              options={prizes
                .filter(
                  (item) =>
                    !configuration?.prize_pools.some(
                      (pool) => pool.prize_id === item.id,
                    ),
                )
                .map((item) => ({
                  value: item.id,
                  label: `${item.name} (${item.type === "cash" ? `¥${item.cash_amount}` : `加价 ¥${item.upgrade_price}`})`,
                }))}
            />
          </Form.Item>
          <div className="two-col">
            <Form.Item
              name="probability"
              label="中奖概率 (0-1)"
              rules={[{ required: true }]}
            >
              <InputNumber
                min={0}
                max={1}
                step={0.01}
                style={{ width: "100%" }}
              />
            </Form.Item>
            <Form.Item name="daily_limit" label="每日发放上限 (0 不限)">
              <InputNumber min={0} style={{ width: "100%" }} />
            </Form.Item>
          </div>
          <Button htmlType="submit" type="primary" block>
            加入奖池
          </Button>
        </Form>
      </Modal>
      <Modal open={modal === "policy"} title={editingPolicy ? "基于当前策略发布新版本" : "发布经销商/地区奖池策略"} footer={null} onCancel={() => { setModal(null); setEditingPolicy(undefined); }}>
        <Form
          key={editingPolicy?.id ?? "new"}
          layout="vertical"
          initialValues={editingPolicy ? { ...editingPolicy, prizes: editingPolicy.prizes.map((item) => ({ prize_id: item.prize_id, probability: Number(item.probability), daily_limit: item.daily_limit })) } : { scope: "dealer", daily_limit: 0, prizes: [{ probability: 0.1, daily_limit: 0 }] }}
          onFinish={(values) => {
            if (!selectedActivityId) return;
            const payload = {
              ...values,
              dealer_id: values.scope === "dealer" ? values.dealer_id : undefined,
              province: values.province || "",
              city: values.city || "",
              district: values.district || "",
              prizes: values.prizes.map((item: { prize_id: number; probability: number; daily_limit: number }) => ({ ...item, probability: Number(item.probability), daily_limit: Number(item.daily_limit || 0) })),
            };
            if (editingPolicy) {
              void request(`/api/admin/policies/${editingPolicy.id}`, { method: "PATCH", body: JSON.stringify(payload) }).then(async () => { setModal(null); setEditingPolicy(undefined); await refreshConfiguration(); message.success("策略新版本已发布"); }).catch((error) => message.error(error instanceof Error ? error.message : "发布失败"));
            } else {
              void create(`/api/admin/activities/${selectedActivityId}/policies`, payload);
            }
          }}
        >
          <Form.Item name="scope" label="适用范围" rules={[{ required: true }]}>
            <Select options={[{ value: "dealer", label: "指定经销商" }, { value: "province", label: "省份" }, { value: "city", label: "城市" }, { value: "district", label: "区县" }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, current) => prev.scope !== current.scope}>
            {({ getFieldValue }) => getFieldValue("scope") === "dealer" ? (
              <Form.Item name="dealer_id" label="经销商" rules={[{ required: true }]}><Select options={dealers.filter((item) => item.status === "active").map((item) => ({ value: item.id, label: `${item.dealer_no} · ${item.company_name}` }))} /></Form.Item>
            ) : <div className="two-col">
              <Form.Item name="province" label="省份" rules={[{ required: true }]}><Input /></Form.Item>
              {getFieldValue("scope") !== "province" && <Form.Item name="city" label="城市" rules={[{ required: true }]}><Input /></Form.Item>}
              {getFieldValue("scope") === "district" && <Form.Item name="district" label="区县" rules={[{ required: true }]}><Input /></Form.Item>}
            </div>}
          </Form.Item>
          <Form.Item name="note" label="发布备注"><Input maxLength={512} /></Form.Item>
          <Form.List name="prizes">
            {(fields, { add, remove }) => <>
              {fields.map((field) => <div className="two-col" key={field.key}>
                <Form.Item {...field} name={[field.name, "prize_id"]} label="奖品" rules={[{ required: true }]}><Select options={prizes.filter((item) => item.status === "active").map((item) => ({ value: item.id, label: item.name }))} /></Form.Item>
                <Form.Item {...field} name={[field.name, "probability"]} label="概率 (0-1)" rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} /></Form.Item>
                <Form.Item {...field} name={[field.name, "daily_limit"]} label="日上限 (0 不限)"><InputNumber min={0} style={{ width: "100%" }} /></Form.Item>
                {fields.length > 1 && <Button onClick={() => remove(field.name)}>移除</Button>}
              </div>)}
              <Button onClick={() => add({ probability: 0, daily_limit: 0 })}>增加奖项</Button>
            </>}
          </Form.List>
          <Button htmlType="submit" type="primary" block style={{ marginTop: 16 }}>发布新版本</Button>
        </Form>
      </Modal>
      <Modal open={modal === "adminUser"} title="新建运营账号" footer={null} onCancel={() => setModal(null)}>
        <Form layout="vertical" initialValues={{ role: "operator" }} onFinish={(values) => void request("/api/admin/users", { method: "POST", body: JSON.stringify(values) }).then(() => { setModal(null); message.success("运营账号已创建"); setPage("overview"); setTimeout(() => setPage("system"), 0); }).catch(error => message.error(error instanceof Error ? error.message : "创建失败"))}>
          <Form.Item name="username" label="账号" rules={[{ required: true }, { min: 3, message: "至少 3 个字符" }]}><Input autoComplete="off" /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true }, { min: 8, message: "至少 8 个字符" }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="role" label="角色"><Select options={[{ value: "operator", label: "运营员（常规运营操作）" }, { value: "super_admin", label: "超级管理员（账号与审计管理）" }]} /></Form.Item>
          <Typography.Text type="secondary">请使用独立初始密码，并仅为需要系统管理权限的人员分配超级管理员角色。</Typography.Text>
          <Button htmlType="submit" type="primary" block style={{ marginTop: 16 }}>创建账号</Button>
        </Form>
      </Modal>
    </Layout>
  );
}
createRoot(document.getElementById("root")!).render(
  <ConfigProvider
    theme={{
      token: {
        colorPrimary: "#137a50",
        borderRadius: 6,
        fontFamily: "Inter, PingFang SC, sans-serif",
      },
    }}
  >
    <App>
      <AdminApp />
    </App>
  </ConfigProvider>,
);
