import { useState, type ReactNode } from "react";
import type {
  AuthenticatedUser,
  InstitutionDiscovery,
  InstitutionRole,
} from "../../api/client";
import type { AppRouteId } from "../../auth/routeGuard";
import {
  buildNavigationItems,
  ROLE_LABELS,
  type NavItemDescriptor,
} from "../../tenant/navigation";
import { StatusBadge } from "../common/UiPrimitives";

export interface AppShellProps {
  children: ReactNode;
  authenticated?: boolean;
  user?: AuthenticatedUser | null;
  institutions?: InstitutionDiscovery[];
  selectedInstitution?: InstitutionDiscovery | null;
  currentRoute?: AppRouteId;
  onNavigate?: (route: AppRouteId) => void;
  onSelectInstitution?: (tenantId: string) => void;
  onLogout?: () => void;
  onLogoutAll?: () => void;
  authBusy?: boolean;
}

const SECTION_LABELS: Record<NavItemDescriptor["section"], string> = {
  institution: "Institution",
  academics: "Academic Structure",
  people: "People & Governance",
  account: "Account & System",
  future: "Phase 2 Modules (Disabled)",
};

export function AppShell({
  children,
  authenticated = false,
  user = null,
  institutions = [],
  selectedInstitution = null,
  currentRoute = "institution",
  onNavigate,
  onSelectInstitution,
  onLogout,
  onLogoutAll,
  authBusy = false,
}: AppShellProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const navItems = authenticated
    ? buildNavigationItems(
        selectedInstitution?.roles ?? [],
        selectedInstitution !== null,
      )
    : [];

  const handleNavClick = (route: AppRouteId) => {
    setMobileMenuOpen(false);
    onNavigate?.(route);
  };

  const renderRoleBadges = () => {
    if (!selectedInstitution || selectedInstitution.roles.length === 0) {
      return null;
    }
    return (
      <div className="viora-row" style={{ gap: "0.35rem" }}>
        {selectedInstitution.roles.map((role) => (
          <StatusBadge key={role} tone="brand">
            {ROLE_LABELS[role as InstitutionRole] ?? role}
          </StatusBadge>
        ))}
      </div>
    );
  };

  return (
    <div className="viora-shell">
      <a className="viora-skip-link" href="#main-content">
        Skip to content
      </a>

      <header className="viora-header">
        <div className="viora-header-left">
          {authenticated && (
            <button
              type="button"
              className="viora-btn viora-btn-sm viora-mobile-menu-btn"
              aria-label="Toggle navigation menu"
              aria-expanded={mobileMenuOpen}
              onClick={() => setMobileMenuOpen((prev) => !prev)}
            >
              Menu
            </button>
          )}

          <button
            type="button"
            className="viora-brand"
            onClick={() =>
              handleNavClick(authenticated ? "institution" : "login")
            }
          >
            <span className="viora-brand-mark" aria-hidden="true">
              V
            </span>
            <span>
              <span className="viora-brand-title">VIORA-AI</span>{" "}
              <span className="viora-brand-subtitle">Institutional Portal</span>
            </span>
          </button>

          {authenticated && institutions.length > 0 && (
            <div className="viora-row">
              <label
                htmlFor="institution-switcher"
                style={{
                  fontSize: "0.78rem",
                  fontWeight: 600,
                  color: "var(--color-text-muted)",
                }}
              >
                Institution:
              </label>
              <select
                id="institution-switcher"
                className="viora-select viora-select-inline"
                value={selectedInstitution?.tenant_id ?? ""}
                onChange={(e) => {
                  if (e.target.value) {
                    onSelectInstitution?.(e.target.value);
                  }
                }}
                aria-label="Select active institution"
              >
                {!selectedInstitution && (
                  <option value="">Choose an institution...</option>
                )}
                {institutions.map((inst) => (
                  <option key={inst.tenant_id} value={inst.tenant_id}>
                    {inst.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div className="viora-header-right">
          {authenticated && user ? (
            <>
              {renderRoleBadges()}
              <span
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color: "var(--color-text-main)",
                }}
              >
                {user.display_name}
              </span>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => handleNavClick("account-password")}
              >
                Security
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={authBusy}
                onClick={onLogout}
              >
                Sign out
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm viora-btn-danger"
                disabled={authBusy}
                onClick={onLogoutAll}
                title="Revoke every active session across all browsers and devices"
              >
                Sign out all
              </button>
            </>
          ) : (
            <div className="viora-row">
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => handleNavClick("health")}
              >
                System Health
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm viora-btn-primary"
                onClick={() => handleNavClick("login")}
              >
                Sign in
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="viora-body-frame">
        {authenticated && (
          <nav
            className={`viora-sidebar ${mobileMenuOpen ? "is-open" : ""}`}
            aria-label="Institutional navigation"
          >
            {(
              [
                "institution",
                "academics",
                "people",
                "account",
                "future",
              ] as const
            ).map((section) => {
              const sectionItems = navItems.filter(
                (item) => item.section === section,
              );
              if (sectionItems.length === 0) {
                return null;
              }
              return (
                <div key={section}>
                  <div className="viora-nav-group-title">
                    {SECTION_LABELS[section]}
                  </div>
                  <ul className="viora-nav-list">
                    {sectionItems.map((item) => {
                      const active =
                        item.route !== undefined && item.route === currentRoute;
                      return (
                        <li key={item.id}>
                          <button
                            type="button"
                            className={`viora-nav-item ${active ? "is-active" : ""}`}
                            disabled={item.disabled}
                            aria-disabled={item.disabled ? "true" : undefined}
                            aria-current={active ? "page" : undefined}
                            onClick={() => {
                              if (item.route && !item.disabled) {
                                handleNavClick(item.route);
                              }
                            }}
                          >
                            <span>{item.label}</span>
                            {item.badge && (
                              <span className="viora-nav-badge">
                                {item.badge}
                              </span>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </nav>
        )}

        <main id="main-content" className="viora-main">
          {children}
        </main>
      </div>

      <footer className="viora-footer">
        VIORA-AI Platform &copy; 2026 &mdash; Tenant isolation and authorization
        enforced server-side via PostgreSQL RLS.
      </footer>
    </div>
  );
}
