import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import { clientUsersApi } from "../../api/client";
import type { ClientUser } from "../../types";
import { Chip, EmptyState, Modal, TSwitch, relTime, useConfirm, useToast } from "../ui/ui";

const DASHBOARD_URL = "https://origo-poc.up.railway.app";

function generatePassword(): string {
  const chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$";
  return Array.from({ length: 16 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
}

function AddUserModal({ clientId, onClose, onCreated }: {
  clientId: string;
  onClose: () => void;
  onCreated: (user: ClientUser, password: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState(generatePassword);
  const [role, setRole] = useState("viewer");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const user = await clientUsersApi.create(clientId, {
        email,
        display_name: name,
        password,
        role,
      });
      onCreated(user, password);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof msg === "string" ? msg : "Failed to create user");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal onClose={onClose}>
      <h3>Add client user</h3>
      <div className="ms">Credentials are shown once, the user must change the password on first login.</div>
      <form onSubmit={handleSubmit}>
        <div className="fld">
          <label>Email *</label>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@client.com" />
        </div>
        <div className="fld">
          <label>Display name *</label>
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="fld">
          <label>Temporary password</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ fontFamily: "var(--mono)" }}
            />
            <button type="button" className="btn sm" onClick={() => setPassword(generatePassword())}>Generate</button>
          </div>
          <div className="fh">Minimum 8 characters.</div>
        </div>
        <div className="fld">
          <label>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="viewer">Viewer</option>
            <option value="owner">Owner</option>
          </select>
        </div>

        {error && <p style={{ color: "var(--bad)", fontSize: 12.5, marginBottom: 8 }}>{error}</p>}

        <div className="macts">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn pri" disabled={loading || !email || !name || password.length < 8}>
            {loading ? "Creating..." : "Create user"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function CredentialsModal({ user, password, onClose }: {
  user: ClientUser; password: string; onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const text = `Dashboard: ${DASHBOARD_URL}\nEmail: ${user.email}\nPassword: ${password}\n\nThey will be prompted to change their password on first login.`;

  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <Modal onClose={onClose}>
      <h3>User created</h3>
      <div className="ms">Send these credentials to the client.</div>
      <pre style={{
        background: "var(--s4)", border: "1px solid var(--b1)", borderRadius: 10, padding: 14,
        fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink2)", whiteSpace: "pre-wrap", lineHeight: 1.6,
      }}>
        {text}
      </pre>
      <div className="macts">
        <button className="btn" onClick={onClose}>Done</button>
        <button className="btn pri" onClick={copy}>{copied ? "Copied" : "Copy to clipboard"}</button>
      </div>
    </Modal>
  );
}

export function ClientUsers() {
  const { clientId } = useParams<{ clientId: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [showAdd, setShowAdd] = useState(false);
  const [credentials, setCredentials] = useState<{ user: ClientUser; password: string } | null>(null);
  const [resetModal, setResetModal] = useState<ClientUser | null>(null);
  const [resetPw, setResetPw] = useState("");

  const { data: users = [], isLoading } = useQuery({
    queryKey: ["admin-client-users", clientId],
    queryFn: () => clientUsersApi.list(clientId!),
    enabled: !!clientId,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-client-users", clientId] });

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      clientUsersApi.setActive(clientId!, id, active),
    onSuccess: (_d, v) => { invalidate(); toast(v.active ? "User activated" : "User deactivated"); },
  });

  const resetMut = useMutation({
    mutationFn: ({ id, pw }: { id: string; pw: string }) =>
      clientUsersApi.resetPassword(clientId!, id, pw),
    onSuccess: () => { invalidate(); setResetModal(null); setResetPw(""); toast("Password reset"); },
  });

  const owners = users.filter((u) => u.role === "owner").length;
  const viewers = users.filter((u) => u.role === "viewer").length;
  const active = users.filter((u) => u.is_active).length;
  const mustChange = users.filter((u) => u.must_change_password).length;

  async function handleToggle(u: ClientUser) {
    if (u.is_active) {
      const ok = await confirm({
        title: "Deactivate user?",
        message: `${u.email} will no longer be able to log in.`,
        confirmLabel: "Deactivate",
        danger: true,
      });
      if (!ok) return;
    }
    toggleMut.mutate({ id: u.id, active: !u.is_active });
  }

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="lbl">Users</div>
          <div className="val">{users.length}</div>
          <div className="hint">{owners} owner, {viewers} viewer</div>
        </div>
        <div className="card">
          <div className="lbl">Active</div>
          <div className="val">{active}</div>
          <div className="hint">{users.length - active} deactivated</div>
        </div>
        <div className="card">
          <div className="lbl">Must change password</div>
          <div className="val">{mustChange}</div>
          <div className="hint">temp credentials outstanding</div>
        </div>
        <div className="card">
          <div className="lbl">Dashboard</div>
          <div className="val" style={{ fontSize: 15, paddingTop: 6 }}>{DASHBOARD_URL.replace(/^https?:\/\//, "")}</div>
          <div className="hint">client login URL</div>
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <div style={{ display: "flex", alignItems: "center", padding: "14px 16px", borderBottom: "1px solid var(--bf)" }}>
          <h3 style={{ fontSize: 13.5, fontWeight: 650 }}>Client users</h3>
          <div style={{ flex: 1 }} />
          <button className="btn pri" onClick={() => setShowAdd(true)}>
            <AddRoundedIcon style={{ fontSize: 15 }} /> Add user
          </button>
        </div>

        {isLoading ? (
          <EmptyState>Loading...</EmptyState>
        ) : users.length === 0 ? (
          <EmptyState>No users yet. Add the first user above.</EmptyState>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tb">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Last login</th>
                  <th>Active</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td className="mono" style={{ fontSize: 12 }}>{u.email}</td>
                    <td>{u.display_name}</td>
                    <td><Chip>{u.role}</Chip></td>
                    <td className="dim2">{u.last_login_at ? relTime(u.last_login_at) : "never"}</td>
                    <td>
                      <TSwitch
                        on={u.is_active}
                        onToggle={() => handleToggle(u)}
                        label={u.is_active ? `Deactivate ${u.email}` : `Activate ${u.email}`}
                      />
                    </td>
                    <td>
                      <button className="btn sm" onClick={() => { setResetModal(u); setResetPw(""); }}>
                        Reset password
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {resetModal && (
        <Modal onClose={() => setResetModal(null)}>
          <h3>Reset password</h3>
          <div className="ms">Set a new temporary password for {resetModal.email}.</div>
          <div className="fld">
            <label>New password</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                type="text"
                value={resetPw}
                onChange={(e) => setResetPw(e.target.value)}
                placeholder="Min 8 characters"
                style={{ fontFamily: "var(--mono)" }}
              />
              <button type="button" className="btn sm" onClick={() => setResetPw(generatePassword())}>Generate</button>
            </div>
          </div>
          <div className="macts">
            <button className="btn" onClick={() => setResetModal(null)}>Cancel</button>
            <button
              className="btn pri"
              disabled={resetPw.length < 8 || resetMut.isPending}
              onClick={() => resetMut.mutate({ id: resetModal.id, pw: resetPw })}
            >
              {resetMut.isPending ? "Saving..." : "Reset"}
            </button>
          </div>
        </Modal>
      )}

      {showAdd && (
        <AddUserModal
          clientId={clientId!}
          onClose={() => setShowAdd(false)}
          onCreated={(user, password) => {
            invalidate();
            setShowAdd(false);
            setCredentials({ user, password });
          }}
        />
      )}

      {credentials && (
        <CredentialsModal
          user={credentials.user}
          password={credentials.password}
          onClose={() => setCredentials(null)}
        />
      )}
    </>
  );
}
