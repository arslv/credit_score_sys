import { Link, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

interface NavItem {
  to: string;
  label: string;
  roles: string[];
}

const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Дашборд", roles: ["risk_manager", "viewer"] },
  { to: "/applications", label: "Заявки", roles: ["risk_manager", "admin", "viewer", "compliance"] },
  { to: "/clients", label: "Клиенты", roles: ["risk_manager", "admin", "viewer", "compliance"] },
  { to: "/contracts", label: "Договоры", roles: ["risk_manager", "admin", "viewer", "compliance"] },
  { to: "/compliance", label: "Комплаенс", roles: ["compliance"] },
  { to: "/rulesets", label: "Правила и пороги", roles: ["risk_manager"] },
  { to: "/imports", label: "Импорт данных", roles: ["admin", "risk_manager"] },
  { to: "/references", label: "Справочники", roles: ["risk_manager", "admin"] },
  { to: "/employees", label: "Сотрудники", roles: ["admin"] },
  { to: "/audit", label: "Аудит", roles: ["admin"] },
  { to: "/system", label: "Состояние системы", roles: ["admin"] },
];

export function Layout() {
  const { employee, logout } = useAuth();
  const location = useLocation();

  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-header__nav">
          {NAV_ITEMS.filter((item) => employee && item.roles.includes(employee.role)).map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={location.pathname.startsWith(item.to) ? "app-header__nav-active" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="app-header__user">
          {employee && (
            <span>
              {employee.full_name} · {employee.role}
            </span>
          )}
          <button className="btn btn-small" onClick={() => void logout()}>
            Выйти
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
