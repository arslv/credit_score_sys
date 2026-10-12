import { Link, Outlet, useLocation } from "react-router-dom";
import { BarChart3, Briefcase, ClipboardList, HelpCircle, Settings, SlidersHorizontal, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { roleLabel } from "../lib/labels";

// T17, шаг 3 (навигация+роли): 5 разделов вместо плоского списка из 11
// вкладок (ваш брифинг — «Пустых разделов, которые роль не может
// использовать, в меню нет»). Разделы с несколькими экранами (Портфель,
// Настройки) получают вторичную строку вкладок внутри раздела — те же
// маршруты, что и раньше, просто сгруппированы по смыслу, а не по тому, в
// каком порядке появлялись в задачах.
// T23: у Настроек вместо вкладок — подменю слева (Сотрудники, Справочники,
// Импорт, Комплаенс, Аудит, Состояние системы — что доступно роли).
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
    ],
  },
  {
    key: "help",
    label: "Справка",
    icon: HelpCircle,
    items: [
      {
        to: "/help", label: "Справка",
        roles: ["risk_manager", "admin", "viewer", "compliance", "operator"],
      },
    ],
  },
  {
    key: "settings",
    label: "Настройки",
    icon: Settings,
    items: [
      { to: "/employees", label: "Сотрудники", roles: ["admin"] },
      { to: "/references", label: "Справочники", roles: ["risk_manager", "admin"] },
      // T23: «Импорт» — в Настройках (было — вкладкой Портфеля).
      { to: "/imports", label: "Импорт", roles: ["admin", "risk_manager"] },
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
        {/* T23: пользователь и «Выйти» — внизу меню; верхняя полоса только
            под вкладки раздела, без пустой строки ради одной кнопки. */}
        <div className="sidebar-foot">
          {employee && (
            <div className="sidebar-foot__user">
              <div className="sidebar-foot__name" title={employee.full_name}>{employee.full_name}</div>
              <div>{roleLabel(employee.role)}</div>
            </div>
          )}
          <button className="btn btn-small" onClick={() => void logout()}>
            Выйти
          </button>
        </div>
      </nav>
      <div className="app-shell-v2__body">
        {/* Портфель — вкладки сверху; Настройки — подменю слева (T23). */}
        {activeGroup && activeGroup.key !== "settings" && activeGroup.items.length > 1 && (
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
          </div>
        )}
        <main className="app-main-v2">
          {activeGroup?.key === "settings" ? (
            <div className="settings-layout">
              <nav className="settings-nav" aria-label="Настройки">
                <div className="settings-nav__title">Настройки</div>
                {activeGroup.items.map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    className={
                      location.pathname.startsWith(item.to)
                        ? "settings-nav__item settings-nav__item--active"
                        : "settings-nav__item"
                    }
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
              <div>
                <Outlet />
              </div>
            </div>
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  );
}
