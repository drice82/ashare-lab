import { lazy, Suspense, useEffect, useState } from "react";
import {
  BookOpen,
  Bell,
  Database,
  CalendarDays,
  ChartNoAxesCombined,
  Layers,
  Filter,
  FlaskConical,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Shield,
  X,
  Bookmark,
} from "lucide-react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { setAccessToken } from "./api";
import { usePageViews } from "./analytics";

import DataManagement from "./pages/DataManagement";
import Screening from "./pages/Screening";
import { Loading } from "./components/common";
import { Button } from "./components/ui/button";
import { UserSessionProvider, useUserSession } from "./userSession";
import { WatchlistProvider } from "./watchlists";

const Dca = lazy(() => import("./pages/Dca"));
const Backtest = lazy(() => import("./pages/Backtest"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const Knowledge = lazy(() => import("./pages/Knowledge"));

const Notifications = lazy(() => import("./pages/Notifications"));

const StockDetail = lazy(() => import("./pages/StockDetail"));
const Watchlists = lazy(() => import("./pages/Watchlists"));

const workspaceNavigation = [
  { to: "/screening", label: "股票筛选", icon: Filter },
  { to: "/dca", label: "定投模拟", icon: CalendarDays },
  { to: "/backtest", label: "单股回测", icon: ChartNoAxesCombined },
  { to: "/portfolio", label: "组合回测", icon: Layers },
  { to: "/knowledge", label: "知识库", icon: BookOpen },
];
const userNavigation = [
  { to: "/notifications", label: "信号通知", icon: Bell },
  { to: "/watchlists", label: "自选列表", icon: Bookmark },
];
const navigationGroups = [
  { label: "工作空间", items: workspaceNavigation },
  { label: "用户空间", items: userNavigation },
];
export default function App() {
  return (
    <UserSessionProvider>
      <WatchlistProvider>
        <Workspace />
      </WatchlistProvider>
    </UserSessionProvider>
  );
}
function Workspace() {
  usePageViews();
  const user = useUserSession();
  const [userError, setUserError] = useState("");
  const [menu, setMenu] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [auth, setAuth] = useState(false);
  const [token, setToken] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const open = () => setAuth(true);
    window.addEventListener("ashare-auth-required", open);
    return () => window.removeEventListener("ashare-auth-required", open);
  }, []);
  function authenticate() {
    setAccessToken(token);
    setToken("");
    setAuth(false);
    setRevision((v) => v + 1);
  }
  return (
    <div className="min-h-screen">
      <aside
        id="workspace-sidebar"
        className={`fixed inset-y-0 left-0 z-30 flex w-60 flex-col overflow-y-auto bg-[#18372c] text-[#d4e0d5] transition-transform lg:translate-x-0 ${sidebarCollapsed ? "lg:hidden" : ""} ${menu ? "translate-x-0" : "-translate-x-full"}`}
      >
        <Link to="/" className="flex items-center gap-3 px-6 pb-8 pt-8">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#54765d] text-[#cfdeb6]">
            <FlaskConical size={20} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-white">
            ashare-lab
            <span className="mt-1 block text-[10px] font-normal tracking-[.15em] text-[#98b099]">
              研究工作台
            </span>
          </span>
        </Link>
        {navigationGroups.map((group) => (
          <div key={group.label} className="mb-5 shrink-0">
            <p className="px-6 text-[10px] tracking-[.16em] text-[#829b85]">
              {group.label}
            </p>
            <nav aria-label={group.label} className="mt-3 space-y-1 px-3">
              {group.items.map((item) => (
                <NavLink
                  end={item.to === "/"}
                  key={item.to}
                  to={item.to}
                  onClick={() => setMenu(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-lg px-4 py-3 text-sm ${isActive ? "bg-[#2b4d39] text-white" : "hover:bg-[#234535]"}`
                  }
                >
                  <item.icon size={17} />
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
        ))}
        <div className="mx-6 my-6 border-t border-[#38533e]" />
        <p className="px-6 text-[10px] tracking-[.16em] text-[#829b85]">
          后续研究能力
        </p>
        <div className="mt-3 space-y-4 px-6 text-sm text-[#839984]">
          {["策略管理"].map((label) => (
            <div key={label} className="flex items-center justify-between">
              <span>{label}</span>
              <span className="text-[9px] text-[#9daf92]">计划开发</span>
            </div>
          ))}
        </div>
        <div className="mt-auto border-t border-[#38533e] p-6 text-[11px] leading-6 text-[#8ea68d]">
          <p>V0.4 / 组合回测研究版</p>
          <NavLink
            to="/data"
            onClick={() => setMenu(false)}
            className={({ isActive }) =>
              `mt-2 inline-flex items-center gap-1.5 text-[10px] ${isActive ? "text-[#d4e0d5] underline" : "text-[#829b85] hover:text-[#d4e0d5]"}`
            }
          >
            <Database size={12} />
            后台管理
          </NavLink>
        </div>
      </aside>
      {menu && (
        <button
          aria-label="关闭导航遮罩"
          className="fixed inset-0 z-20 bg-black/30 lg:hidden"
          onClick={() => setMenu(false)}
        />
      )}
      <div className={sidebarCollapsed ? "" : "lg:ml-60"}>
        <header className="flex h-[72px] items-center justify-between border-b border-border bg-white/80 px-5 md:px-9">
          <div className="flex items-center gap-3">
            <button
              className="lg:hidden"
              aria-label="打开导航"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <button
              type="button"
              className="hidden rounded-md p-1 text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-emerald-600 lg:inline-flex"
              aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
              title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
              aria-expanded={!sidebarCollapsed}
              aria-controls="workspace-sidebar"
              onClick={() => setSidebarCollapsed((value) => !value)}
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen size={20} />
              ) : (
                <PanelLeftClose size={20} />
              )}
            </button>
            <span className="text-xs text-muted-foreground">
              研究工作台 <span className="mx-2 text-[#b9c5b6]">/</span> 沪深 A
              股
            </span>
          </div>
          <div className="flex items-center gap-4">
            {user.session ? (
              <div className="flex items-center gap-2 text-xs">
                <span className="hidden sm:inline">{user.session.email}</span>
                <button
                  onClick={() => {
                    setUserError("");
                    void user
                      .signOut()
                      .catch((e) =>
                        setUserError(
                          e instanceof Error ? e.message : "退出失败",
                        ),
                      );
                  }}
                >
                  退出
                </button>
              </div>
            ) : (
              <button className="text-xs" onClick={user.openLogin}>
                登录
              </button>
            )}
            <span className="hidden text-xs text-muted-foreground sm:block">
              收盘后研究 · Asia/Shanghai
            </span>
            <span className="badge border border-[#d8e5d3]">V0.4</span>
            <button
              title="配置访问密钥"
              aria-label="配置访问密钥"
              className="text-muted-foreground"
              onClick={() => setAuth(true)}
            >
              <Shield size={16} />
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] p-5 md:p-9" key={revision}>
          {userError && (
            <p role="alert" className="mb-3 text-sm text-red-700">
              {userError}
            </p>
          )}
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Navigate to="/screening" replace />} />
              <Route path="/data" element={<DataManagement />} />
              <Route path="/screening" element={<Screening />} />
              <Route path="/dca" element={<Dca />} />
              <Route path="/backtest" element={<Backtest />} />
              <Route path="/portfolio" element={<Portfolio />} />
              <Route path="/watchlists" element={<Watchlists />} />
              <Route path="/notifications" element={<Notifications />} />
              <Route
                path="/notifications/unsubscribe"
                element={<Notifications unsubscribe />}
              />
              <Route path="/knowledge" element={<Knowledge />} />
              <Route path="/stock/:code" element={<StockDetail />} />
              <Route
                path="*"
                element={
                  <div className="py-10">
                    页面不存在。
                    <Link to="/" className="ml-3 underline">
                      返回首页
                    </Link>
                  </div>
                }
              />
            </Routes>
          </Suspense>
        </main>
        <footer className="w-full px-5 pb-6 text-center text-[10px] text-muted-foreground">
          ashare-lab · 可复核的个人研究工具 · 筛选结果用于进一步研究
        </footer>
      </div>
      {auth && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-5">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              authenticate();
            }}
            className="w-full max-w-sm rounded-xl bg-white p-6 shadow-xl"
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-semibold">访问密钥</h2>
              <button
                type="button"
                aria-label="关闭密钥输入"
                onClick={() => setAuth(false)}
              >
                <X size={16} />
              </button>
            </div>
            <p className="mb-4 text-xs leading-5 text-muted-foreground">
              输入服务器配置的
              API_ACCESS_TOKEN。密钥仅保存在当前页面内存中，刷新后需要重新输入。
            </p>
            <label>
              密钥
              <input
                autoFocus
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <Button type="submit" className="mt-4 w-full" disabled={!token}>
              连接工作台
            </Button>
          </form>
        </div>
      )}
    </div>
  );
}
