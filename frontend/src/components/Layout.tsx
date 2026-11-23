import { Link, Outlet, useLocation } from "react-router-dom";
import { BarChart3, Briefcase, ClipboardList, HelpCircle, LogOut, Settings, ShieldAlert, SlidersHorizontal, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { roleLabel } from "../lib/labels";
import { Logo } from "./Logo";
import { ThemeSwitch } from "./ThemeSwitch";

// T17, шаг 3 (навигация+роли): 5 разделов вместо плоского списка из 11
// вкладок (ваш брифинг — «Пустых разделов, которые роль не может
// использовать, в меню нет»). Разделы с несколькими экранами (Портфель,
// Настройки) получают вторичную строку вкладок внутри раздела — те же
// маршруты, что и раньше, просто сгруппированы по смыслу, а не по тому, в
// каком порядке появлялись в задачах.
// T23: у Настроек вместо вкладок — подменю слева (Сотрудники, Чёрный список,
// Импорт, Логи, Состояние системы — что доступно роли).
// T23 (решение владельца): ролей три — admin, operator, viewer. «Проверка по
// перечням» (бывший «Комплаенс») — отдельный раздел меню: это очередь работы,
// а не настройка.
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
  /** Внизу меню, у темы и пользователя (Справка). */
  foot?: boolean;
}

// Порядок меню (отзыв владельца 24.11.2026): Заявки, Росфинмониторинг,
// Правила и пороги, Портфель, Настройки, Аналитика — последней в списке;
// Справка — внизу, рядом с темой и пользователем.
const NAV_GROUPS: NavGroup[] = [
  {
    key: "applications",
    label: "Заявки",
    icon: ClipboardList,
    items: [
      { to: "/applications", label: "Заявки", roles: ["admin", "operator", "viewer"] },
    ],
  },
  {
    key: "watchlist",
    label: "Росфинмониторинг",
    icon: ShieldAlert,
    items: [{ to: "/compliance", label: "Росфинмониторинг", roles: ["admin", "operator"] }],
  },
  {
    key: "scoring",
    label: "Правила и пороги",
    icon: SlidersHorizontal,
    items: [{ to: "/rulesets", label: "Правила и пороги", roles: ["admin"] }],
  },
  {
    key: "portfolio",
    label: "Портфель",
    icon: Briefcase,
    items: [
      { to: "/clients", label: "Клиенты", roles: ["admin", "operator", "viewer"] },
    ],
  },
  {
    key: "settings",
    label: "Настройки",
    icon: Settings,
    items: [
      { to: "/employees", label: "Сотрудники", roles: ["admin"] },
      { to: "/references", label: "Чёрный список", roles: ["admin"] },
      // Договоры, их импорт и распознавание паспорта скрыты из меню (по
      // решению владельца: договоры ведёт CRM, распознавания пока нет) —
      // страницы /contracts, /imports, /settings/recognition открываются
      // только по прямому адресу.
      { to: "/audit", label: "Логи", roles: ["admin"] },
      { to: "/system", label: "Состояние системы", roles: ["admin"] },
    ],
  },
  {
    key: "analytics",
    label: "Аналитика",
    icon: BarChart3,
    items: [{ to: "/dashboard", label: "Аналитика", roles: ["admin", "viewer"] }],
  },
  {
    key: "help",
    label: "Справка",
    icon: HelpCircle,
    foot: true,
    items: [{ to: "/help", label: "Справка", roles: ["admin", "operator", "viewer"] }],
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

  // Переход по разделу — на первый доступный этой роли экран внутри него.
  function navLink(group: (typeof visibleGroups)[number]) {
    const Icon = group.icon;
    const isActive = group.key === activeGroup?.key;
    return (
      <Link
        key={group.key}
        to={group.items[0].to}
        className={isActive ? "sidebar-item sidebar-item--active" : "sidebar-item"}
        aria-current={isActive ? "page" : undefined}
      >
        <Icon size={19} strokeWidth={1.75} />
        <span>{group.label}</span>
      </Link>
    );
  }

  return (
    <div className="app-shell-v2">
      <nav className="sidebar">
        <div className="sidebar-logo">
          <Logo />
        </div>
        <div className="sidebar-list">
          {visibleGroups.filter((group) => !group.foot).map((group) => navLink(group))}
        </div>
        {/* Низ меню: Справка, тема, пользователь и «Выйти». */}
        <div className="sidebar-foot">
          {visibleGroups.filter((group) => group.foot).map((group) => navLink(group))}
          <ThemeSwitch />
          {employee && (
            <div className="sidebar-user">
              <span className="sidebar-user__avatar" aria-hidden="true">
                {initials(employee.full_name)}
              </span>
              <div className="sidebar-user__text">
                <div className="sidebar-user__name" title={employee.full_name}>{employee.full_name}</div>
                <div className="sidebar-user__role">{roleLabel(employee.role)}</div>
              </div>
              <button
                type="button"
                className="sidebar-user__logout"
                onClick={() => void logout()}
                aria-label="Выйти"
                title="Выйти"
              >
                <LogOut size={17} strokeWidth={1.75} />
              </button>
            </div>
          )}
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

// «Арсланов Арслан» → «АА»: кружок с инициалами у пользователя внизу меню.
function initials(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") || "?";
}
