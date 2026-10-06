import { Link, Outlet, useLocation } from "react-router-dom";
import { BarChart3, Briefcase, ClipboardList, Settings, SlidersHorizontal, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";

// T17, шаг 3 (навигация+роли): 5 разделов вместо плоского списка из 11
// вкладок (ваш брифинг — «Пустых разделов, которые роль не может
// использовать, в меню нет»). Разделы с несколькими экранами (Портфель,
// Настройки) получают вторичную строку вкладок внутри раздела — те же
// маршруты, что и раньше, просто сгруппированы по смыслу, а не по тому, в
// каком порядке появлялись в задачах.
interface NavItem {
  to: string;
  label: string;
  roles: string[];
}

interface NavGroup {
  key: string;
  label: string;
  icon: LucideIcon;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    key: "applications",
    label: "Заявки",
    icon: ClipboardList,
    items: [
      { to: "/applications", label: "Заявки", roles: ["risk_manager", "admin", "viewer", "compliance", "operator"] },
    ],
  },
  {
    key: "scoring",
    label: "Скоринг",
    icon: SlidersHorizontal,
    items: [{ to: "/rulesets", label: "Скоринг", roles: ["risk_manager"] }],
  },
  {
    key: "analytics",
    label: "Аналитика",
    icon: BarChart3,
    items: [{ to: "/dashboard", label: "Аналитика", roles: ["risk_manager", "viewer"] }],
  },
  {
    key: "portfolio",
    label: "Портфель",
    icon: Briefcase,
    items: [
      { to: "/contracts", label: "Договоры", roles: ["risk_manager", "admin", "viewer", "compliance"] },
      { to: "/clients", label: "Клиенты", roles: ["risk_manager", "admin", "viewer", "compliance"] },
      { to: "/imports", label: "Импорт", roles: ["admin", "risk_manager"] },
    ],
  },
  {
    key: "settings",
    label: "Настройки",
    icon: Settings,
    items: [
      { to: "/employees", label: "Сотрудники", roles: ["admin"] },
      { to: "/references", label: "Справочники", roles: ["risk_manager", "admin"] },
      { to: "/compliance", label: "Комплаенс", roles: ["compliance"] },
      { to: "/audit", label: "Аудит", roles: ["admin"] },
      { to: "/system", label: "Состояние системы", roles: ["admin"] },
    ],
  },
];

export function Layout() {
  const { employee, logout } = useAuth();
  const location = useLocation();
  const role = employee?.role;

  const visibleGroups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => role && item.roles.includes(role)),
  })).filter((group) => group.items.length > 0);

  const activeGroup = visibleGroups.find((group) =>
    group.items.some((item) => location.pathname.startsWith(item.to)),
  );

  return (
    <div className="app-shell-v2">
      <nav className="sidebar">
        <div className="sidebar-logo">DIALOG Scoring</div>
        {visibleGroups.map((group) => {
          const Icon = group.icon;
          const isActive = group.key === activeGroup?.key;
          // Переход по разделу — на первый доступный этой роли экран внутри него.
          return (
            <Link
              key={group.key}
              to={group.items[0].to}
              className={isActive ? "sidebar-item sidebar-item--active" : "sidebar-item"}
            >
              <Icon size={17} strokeWidth={1.75} />
              {group.label}
            </Link>
          );
        })}
        <div className="sidebar-foot">
          {employee && (
            <>
              {employee.full_name}
              <br />
              <span className="muted">{employee.role}</span>
            </>
          )}
        </div>
      </nav>
      <div className="app-shell-v2__body">
        {activeGroup && activeGroup.items.length > 1 && (
          <div className="subnav">
            {activeGroup.items.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={location.pathname.startsWith(item.to) ? "subnav-item subnav-item--active" : "subnav-item"}
              >
                {item.label}
              </Link>
            ))}
            <button className="btn btn-small subnav-logout" onClick={() => void logout()}>
              Выйти
            </button>
          </div>
        )}
        {(!activeGroup || activeGroup.items.length <= 1) && (
          <div className="subnav subnav--minimal">
            <span />
            <button className="btn btn-small subnav-logout" onClick={() => void logout()}>
              Выйти
            </button>
          </div>
        )}
        <main className="app-main-v2">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
