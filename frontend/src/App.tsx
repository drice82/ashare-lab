import { lazy, Suspense, useEffect, useState } from "react";
import {
  Database,
  Filter,
  FlaskConical,
  LayoutDashboard,
  Menu,
  Shield,
  X,
} from "lucide-react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { setAccessToken } from "./api";
import Dashboard from "./pages/Dashboard";
import DataManagement from "./pages/DataManagement";
import Screening from "./pages/Screening";
import { Loading } from "./components/common";
import { Button } from "./components/ui/button";

const Dca = lazy(() => import("./pages/Dca"));
const Backtest = lazy(() => import("./pages/Backtest"));

const StockDetail = lazy(() => import("./pages/StockDetail"));

const navigation = [
  { to: "/", label: "研究概览", icon: LayoutDashboard },
  { to: "/data", label: "数据管理", icon: Database },
  { to: "/screening", label: "股票筛选", icon: Filter },
  { to: "/dca", label: "定投模拟", icon: FlaskConical },
  { to: "/backtest", label: "单股回测", icon: FlaskConical },
];
export default function App() {
  const [menu, setMenu] = useState(false);
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
        className={`fixed inset-y-0 left-0 z-30 flex w-60 flex-col bg-[#18372c] text-[#d4e0d5] transition-transform lg:translate-x-0 ${menu ? "translate-x-0" : "-translate-x-full"}`}
      >
        <Link to="/" className="flex items-center gap-3 px-6 pb-8 pt-8">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#54765d] text-[#cfdeb6]">
            <FlaskConical size={20} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-white">
            ashare<span className="font-normal text-[#a9bd9b]"> / lab</span>
            <span className="mt-1 block text-[10px] font-normal tracking-[.15em] text-[#98b099]">
              A 股量化研究工作台
            </span>
          </span>
        </Link>
        <p className="px-6 text-[10px] tracking-[.16em] text-[#829b85]">
          工作空间
        </p>
        <nav className="mt-3 space-y-1 px-3">
          {navigation.map((item) => (
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
        <div className="mx-6 my-6 border-t border-[#38533e]" />
        <p className="px-6 text-[10px] tracking-[.16em] text-[#829b85]">
          后续研究能力
        </p>
        <div className="mt-3 space-y-4 px-6 text-sm text-[#839984]">
          {["组合回测", "策略管理", "信号通知"].map((label) => (
            <div key={label} className="flex items-center justify-between">
              <span>{label}</span>
              <span className="text-[9px] text-[#9daf92]">计划开发</span>
            </div>
          ))}
        </div>
        <div className="mt-auto border-t border-[#38533e] p-6 text-[11px] leading-6 text-[#8ea68d]">
          <p className="flex items-center gap-2">
            <Shield size={13} />
            个人研究 · 本地数据
          </p>
          <p>V0.3 / 单股回测研究版</p>
        </div>
      </aside>
      {menu && (
        <button
          aria-label="关闭导航遮罩"
          className="fixed inset-0 z-20 bg-black/30 lg:hidden"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="lg:ml-60">
        <header className="flex h-[72px] items-center justify-between border-b border-border bg-white/80 px-5 md:px-9">
          <div className="flex items-center gap-3">
            <button
              className="lg:hidden"
              aria-label="打开导航"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span className="text-xs text-muted-foreground">
              研究工作台 <span className="mx-2 text-[#b9c5b6]">/</span> 沪深 A
              股
            </span>
          </div>
          <div className="flex items-center gap-4">
            <span className="hidden text-xs text-muted-foreground sm:block">
              收盘后研究 · Asia/Shanghai
            </span>
            <span className="badge border border-[#d8e5d3]">V0.3</span>
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
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/data" element={<DataManagement />} />
              <Route path="/screening" element={<Screening />} />
              <Route path="/dca" element={<Dca />} />
              <Route path="/backtest" element={<Backtest />} />
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
        <footer className="px-9 pb-6 text-[10px] text-muted-foreground">
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
