'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  AuthUser,
  AuditLogEntry,
  listUsers,
  updateUser,
  deleteUser,
  resetUserPassword,
  listAuditLogs,
} from '@/lib/api';
import { useAuth, useIsAdmin } from '@/context/AuthContext';
import {
  Users,
  UserCheck,
  UserPlus,
  Shield,
  ShieldCheck,
  KeyRound,
  Ban,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Clock,
  History,
  X,
  Trash2,
} from 'lucide-react';
import AddPersonModal from './AddPersonModal';
import ConfirmDialog from '@/components/ui/ConfirmDialog';

const TEAMS = ['School', 'Corporate', 'QShala', 'Consultant'] as const;

interface ConfirmModalState {
  isOpen: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel: string;
  isDestructive: boolean;
  isLoading: boolean;
  error: string | null;
  action: () => Promise<void>;
}

export default function AccessTab() {
  const { user: currentAdmin } = useAuth();
  const isAdmin = useIsAdmin();

  const [users, setUsers] = useState<AuthUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [loadingAudit, setLoadingAudit] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Modals state
  const [showAddPersonModal, setShowAddPersonModal] = useState(false);
  const [resetPasswordTarget, setResetPasswordTarget] = useState<AuthUser | null>(null);

  // Confirmation Dialog State
  const [confirmState, setConfirmState] = useState<ConfirmModalState>({
    isOpen: false,
    title: '',
    message: '',
    confirmLabel: 'Confirm',
    isDestructive: false,
    isLoading: false,
    error: null,
    action: async () => {},
  });

  // Reset Password Form State
  const [resetNewPasswordVal, setResetNewPasswordVal] = useState('');
  const [resetMustChange, setResetMustChange] = useState(true);
  const [isSubmittingReset, setIsSubmittingReset] = useState(false);

  // Fetch users & audit logs
  const loadData = useCallback(async () => {
    if (!isAdmin) return;
    setErrorMessage(null);
    try {
      setLoadingUsers(true);
      const usersData = await listUsers({ limit: 100 });
      setUsers(usersData.items || usersData.users || []);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to load users');
    } finally {
      setLoadingUsers(false);
    }

    try {
      setLoadingAudit(true);
      const auditData = await listAuditLogs({ limit: 30 });
      setAuditLogs(auditData.items || []);
    } catch {
      // Audit log error non-blocking
    } finally {
      setLoadingAudit(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    let mounted = true;
    if (!isAdmin) return;

    listUsers({ limit: 100 })
      .then((usersData) => {
        if (mounted) {
          setUsers(usersData.items || usersData.users || []);
          setLoadingUsers(false);
        }
      })
      .catch((err: unknown) => {
        if (mounted) {
          setErrorMessage(err instanceof Error ? err.message : 'Failed to load users');
          setLoadingUsers(false);
        }
      });

    listAuditLogs({ limit: 30 })
      .then((auditData) => {
        if (mounted) {
          setAuditLogs(auditData.items || []);
          setLoadingAudit(false);
        }
      })
      .catch(() => {
        if (mounted) {
          setLoadingAudit(false);
        }
      });

    return () => {
      mounted = false;
    };
  }, [isAdmin]);

  // Count active admins to guard last admin
  const activeAdminsCount = useMemo(() => {
    return users.filter((u) => u.role === 'admin' && u.status === 'active').length;
  }, [users]);

  // Handle Team Change
  const handleTeamChange = async (targetUser: AuthUser, teamId: string) => {
    try {
      setErrorMessage(null);
      await updateUser(targetUser.id, { team_id: teamId });
      setSuccessMessage(`Updated team for ${targetUser.display_name} to ${teamId}`);
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update team');
    }
  };

  // Confirmation Trigger: Role Toggle (Grant / Remove Admin)
  const triggerRoleToggleConfirm = (targetUser: AuthUser) => {
    if (targetUser.provider !== 'microsoft') {
      setErrorMessage('Only Microsoft provider accounts can be granted Admin privileges.');
      return;
    }

    if (targetUser.role === 'admin' && activeAdminsCount <= 1) {
      setErrorMessage('Cannot remove the last active administrator.');
      return;
    }

    const granting = targetUser.role !== 'admin';
    const newRole = granting ? 'admin' : 'member';

    setConfirmState({
      isOpen: true,
      title: granting
        ? `Make ${targetUser.display_name} an admin?`
        : `Remove admin from ${targetUser.display_name}?`,
      message: granting
        ? `${targetUser.display_name} will be granted administrator privileges. They will have full access to user management, system settings, and security controls.`
        : `${targetUser.display_name} will become a standard member. They will lose access to user management and system settings.`,
      confirmLabel: granting ? 'Grant Admin' : 'Remove Admin',
      isDestructive: !granting,
      isLoading: false,
      error: null,
      action: async () => {
        await updateUser(targetUser.id, { role: newRole });
        setSuccessMessage(`Updated ${targetUser.display_name} to ${newRole}`);
      },
    });
  };

  // Confirmation Trigger: Block / Unblock User
  const triggerStatusToggleConfirm = (targetUser: AuthUser) => {
    if (targetUser.role === 'admin' && targetUser.status === 'active' && activeAdminsCount <= 1) {
      setErrorMessage('Cannot block the last active administrator.');
      return;
    }

    const isBlocking = targetUser.status === 'active';
    const nextStatus = isBlocking ? 'blocked' : 'active';

    if (!isBlocking) {
      // Unblocking can proceed directly or with a mild confirmation
      updateUser(targetUser.id, { status: 'active' })
        .then(() => {
          setSuccessMessage(`Unblocked ${targetUser.display_name}`);
          loadData();
        })
        .catch((err: unknown) => {
          setErrorMessage(err instanceof Error ? err.message : 'Failed to unblock user');
        });
      return;
    }

    setConfirmState({
      isOpen: true,
      title: `Block ${targetUser.display_name}?`,
      message: `This will immediately revoke all active sessions for ${targetUser.display_name}. They will lose access immediately and will not be able to sign in until unblocked.`,
      confirmLabel: 'Block User',
      isDestructive: true,
      isLoading: false,
      error: null,
      action: async () => {
        await updateUser(targetUser.id, { status: nextStatus });
        setSuccessMessage(`Blocked ${targetUser.display_name}`);
      },
    });
  };

  // Confirmation Trigger: Soft Delete User
  const triggerDeleteConfirm = (targetUser: AuthUser) => {
    setConfirmState({
      isOpen: true,
      title: `Delete ${targetUser.display_name}?`,
      message: (
        <div className="space-y-2">
          <p>
            Are you sure you want to delete <strong className="font-semibold text-slate-900">{targetUser.display_name}</strong>?
          </p>
          <p className="text-[12.5px] text-slate-500">
            Their account will be soft-deleted and all active sessions revoked. Existing quizzes and documents they created will preserve their name. Re-adding them later will restore their account.
          </p>
        </div>
      ),
      confirmLabel: 'Delete User',
      isDestructive: true,
      isLoading: false,
      error: null,
      action: async () => {
        await deleteUser(targetUser.id);
        setSuccessMessage(`Deleted ${targetUser.display_name}`);
      },
    });
  };

  // Confirmation Trigger: Decline Pending Request
  const triggerDeclinePendingConfirm = (targetUser: AuthUser) => {
    setConfirmState({
      isOpen: true,
      title: `Decline request from ${targetUser.display_name}?`,
      message: `This will reject the access request and block ${targetUser.display_name} from signing in.`,
      confirmLabel: 'Decline Request',
      isDestructive: true,
      isLoading: false,
      error: null,
      action: async () => {
        await updateUser(targetUser.id, { status: 'blocked' });
        setSuccessMessage(`Declined and blocked ${targetUser.display_name}`);
      },
    });
  };

  // Handle Execute Confirmation
  const handleExecuteConfirm = async () => {
    setConfirmState((prev) => ({ ...prev, isLoading: true, error: null }));
    try {
      await confirmState.action();
      setConfirmState((prev) => ({ ...prev, isOpen: false, isLoading: false }));
      await loadData();
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : 'Operation failed';
      setConfirmState((prev) => ({
        ...prev,
        isLoading: false,
        error: errorMsg,
      }));
    }
  };

  // Handle Approve Pending User
  const handleApprovePending = async (targetUser: AuthUser, teamId: string) => {
    try {
      setErrorMessage(null);
      await updateUser(targetUser.id, { status: 'active', team_id: teamId });
      setSuccessMessage(`Approved ${targetUser.display_name} into team ${teamId}`);
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to approve user');
    }
  };

  // Handle Reset Password Submit
  const handleResetPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetPasswordTarget) return;
    setIsSubmittingReset(true);
    setErrorMessage(null);
    try {
      await resetUserPassword(resetPasswordTarget.id, {
        new_password: resetNewPasswordVal,
        must_change_password: resetMustChange,
      });
      setSuccessMessage(`Password reset for ${resetPasswordTarget.display_name}`);
      setResetPasswordTarget(null);
      setResetNewPasswordVal('');
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to reset password');
    } finally {
      setIsSubmittingReset(false);
    }
  };

  if (!isAdmin) {
    return (
      <div className="rounded-xl border border-slate-200/80 bg-white p-8 text-center text-[14px] text-slate-500">
        Administrator privileges required to view user management.
      </div>
    );
  }

  const pendingUsers = users.filter((u) => u.status === 'pending');
  const activeAndBlockedUsers = users.filter((u) => u.status !== 'pending' && u.status !== 'deleted');

  return (
    <div className="space-y-8">
      {/* Messages */}
      {errorMessage && (
        <div className="flex items-center justify-between rounded-xl border border-red-200 bg-red-50 p-4 text-[13.5px] text-red-800">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
            <span>{errorMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setErrorMessage(null)}
            className="text-red-600 hover:text-red-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {successMessage && (
        <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-[13.5px] text-emerald-800">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
            <span>{successMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setSuccessMessage(null)}
            className="text-emerald-600 hover:text-emerald-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Pending Account Requests Banner */}
      {pendingUsers.length > 0 && (
        <div className="rounded-xl border border-amber-300 bg-amber-50/70 p-6 shadow-sm">
          <div className="flex items-center gap-2.5 border-b border-amber-200/80 pb-3">
            <Clock className="h-5 w-5 text-amber-700" />
            <h2 className="text-[16px] font-bold text-amber-900">
              Pending Account Approvals ({pendingUsers.length})
            </h2>
          </div>
          <p className="mt-2 text-[13px] text-amber-800">
            These Microsoft users have authenticated and are waiting for an administrator to assign their team and activate access.
          </p>

          <div className="mt-4 divide-y divide-amber-200/60 rounded-lg border border-amber-200 bg-white">
            {pendingUsers.map((pending) => (
              <PendingUserRow
                key={pending.id}
                user={pending}
                onApprove={handleApprovePending}
                onDecline={triggerDeclinePendingConfirm}
              />
            ))}
          </div>
        </div>
      )}

      {/* Users Table Header & Action */}
      <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center gap-2.5">
            <Users className="h-5 w-5 text-slate-700" />
            <div>
              <h2 className="text-[17px] font-bold text-slate-900">Team & User Directory</h2>
              <p className="text-[12.5px] text-slate-500">
                Manage roles, team labels, passwords, and system permissions.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={loadData}
              disabled={loadingUsers}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[12.5px] font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none"
              title="Refresh users"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loadingUsers ? 'animate-spin' : ''}`} />
              <span>Refresh</span>
            </button>

            {/* Single Consolidated "Add Person" button */}
            <button
              type="button"
              onClick={() => setShowAddPersonModal(true)}
              className="flex items-center gap-1.5 rounded-lg bg-[#1E222D] px-3.5 py-1.5 text-[12.5px] font-semibold text-[#FFFCF5] shadow-sm transition-colors hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
            >
              <UserPlus className="h-3.5 w-3.5" />
              <span>Add Person</span>
            </button>
          </div>
        </div>

        {/* Directory Table */}
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-slate-200/70 bg-slate-50/75 text-[11.5px] font-bold uppercase tracking-wider text-slate-500">
                <th className="py-3 px-3">Person</th>
                <th className="py-3 px-3">Provider</th>
                <th className="py-3 px-3">Team</th>
                <th className="py-3 px-3">Role</th>
                <th className="py-3 px-3">Status</th>
                <th className="py-3 px-3">Last Active</th>
                <th className="py-3 px-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loadingUsers ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-400">
                    <RefreshCw className="mx-auto mb-2 h-5 w-5 animate-spin text-amber-500" />
                    Loading team directory...
                  </td>
                </tr>
              ) : activeAndBlockedUsers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-400">
                    No users registered yet.
                  </td>
                </tr>
              ) : (
                activeAndBlockedUsers.map((u) => {
                  const isCurrent = currentAdmin?.id === u.id;
                  const isLastAdmin = u.role === 'admin' && u.status === 'active' && activeAdminsCount <= 1;

                  return (
                    <tr key={u.id} className="transition-colors hover:bg-slate-50/80">
                      {/* Name & Identity */}
                      <td className="py-3 px-3">
                        <div className="font-semibold text-slate-900 flex items-center gap-1.5">
                          <span>{u.display_name}</span>
                          {isCurrent && (
                            <span className="rounded bg-slate-100 px-1.5 py-0.2 text-[10.5px] text-slate-600 font-normal">
                              You
                            </span>
                          )}
                        </div>
                        <div className="text-[12px] text-slate-500">{u.email || u.username}</div>
                      </td>

                      {/* Provider */}
                      <td className="py-3 px-3">
                        {u.provider === 'microsoft' ? (
                          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-slate-700">
                            <span className="h-2 w-2 rounded-full bg-blue-500" />
                            Microsoft
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-slate-700">
                            <span className="h-2 w-2 rounded-full bg-amber-500" />
                            Password
                          </span>
                        )}
                      </td>

                      {/* Team Selector */}
                      <td className="py-3 px-3">
                        <select
                          value={u.team_id || ''}
                          onChange={(e) => handleTeamChange(u, e.target.value)}
                          className="rounded-lg border border-slate-200 bg-white py-1 px-2 text-[12.5px] font-medium text-slate-800 shadow-2xs focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-400/20"
                        >
                          <option value="" disabled>
                            Select Team
                          </option>
                          {TEAMS.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </td>

                      {/* Role */}
                      <td className="py-3 px-3">
                        {u.role === 'admin' ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-800 border border-emerald-200/60">
                            <ShieldCheck className="h-3 w-3" />
                            Admin
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-[11.5px] font-medium text-slate-700">
                            Member
                          </span>
                        )}
                      </td>

                      {/* Status */}
                      <td className="py-3 px-3">
                        {u.status === 'active' ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100/70 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-800">
                            Blocked
                          </span>
                        )}
                      </td>

                      {/* Last Active */}
                      <td className="py-3 px-3 text-[12px] text-slate-500">
                        {u.last_login_at
                          ? new Date(u.last_login_at).toLocaleDateString(undefined, {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : 'Never'}
                      </td>

                      {/* Actions */}
                      <td className="py-3 px-3 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {/* Toggle Admin (Microsoft only) with Confirmation */}
                          {u.provider === 'microsoft' && (
                            <button
                              type="button"
                              onClick={() => triggerRoleToggleConfirm(u)}
                              disabled={isLastAdmin}
                              title={
                                isLastAdmin
                                  ? 'Cannot remove the last active administrator'
                                  : u.role === 'admin'
                                  ? 'Demote to Member'
                                  : 'Promote to Admin'
                              }
                              className={`rounded p-1 transition-colors ${
                                u.role === 'admin'
                                  ? 'text-emerald-700 hover:bg-emerald-50 disabled:opacity-40'
                                  : 'text-slate-500 hover:bg-slate-100'
                              }`}
                            >
                              <Shield className="h-4 w-4" />
                            </button>
                          )}

                          {/* Reset Password (Password accounts only) */}
                          {u.provider === 'password' && (
                            <button
                              type="button"
                              onClick={() => setResetPasswordTarget(u)}
                              title="Reset Password"
                              className="rounded p-1 text-amber-700 hover:bg-amber-50"
                            >
                              <KeyRound className="h-4 w-4" />
                            </button>
                          )}

                          {/* Block / Unblock Toggle with Confirmation */}
                          <button
                            type="button"
                            onClick={() => triggerStatusToggleConfirm(u)}
                            disabled={isLastAdmin}
                            title={
                              isLastAdmin
                                ? 'Cannot block the last active administrator'
                                : u.status === 'active'
                                ? 'Block user access'
                                : 'Unblock user'
                            }
                            className={`rounded p-1 transition-colors ${
                              u.status === 'active'
                                ? 'text-slate-400 hover:text-red-700 hover:bg-red-50 disabled:opacity-30'
                                : 'text-emerald-700 hover:bg-emerald-50'
                            }`}
                          >
                            {u.status === 'active' ? (
                              <Ban className="h-4 w-4" />
                            ) : (
                              <CheckCircle2 className="h-4 w-4" />
                            )}
                          </button>

                          {/* Soft Delete Icon (hidden for self and seeded accounts) */}
                          {!isCurrent && !u.is_seeded && (
                            <button
                              type="button"
                              onClick={() => triggerDeleteConfirm(u)}
                              title={`Delete ${u.display_name}`}
                              className="rounded p-1 text-slate-400 hover:text-red-700 hover:bg-red-50 transition-colors"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Audit Log Table */}
      <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <History className="h-4.5 w-4.5 text-slate-700" />
            <h3 className="text-[15px] font-bold text-slate-900">
              Authentication & Security Audit Log
            </h3>
          </div>
          <button
            type="button"
            onClick={loadData}
            disabled={loadingAudit}
            className="flex items-center gap-1 text-[12px] font-semibold text-slate-600 hover:text-slate-900"
          >
            <RefreshCw className={`h-3 w-3 ${loadingAudit ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                <th className="py-2.5 px-3">Time</th>
                <th className="py-2.5 px-3">Action</th>
                <th className="py-2.5 px-3">Actor</th>
                <th className="py-2.5 px-3">Target</th>
                <th className="py-2.5 px-3">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {loadingAudit ? (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-slate-400">
                    Loading audit trail...
                  </td>
                </tr>
              ) : auditLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-slate-400">
                    No audit records found.
                  </td>
                </tr>
              ) : (
                auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50/50">
                    <td className="py-2.5 px-3 text-slate-500 whitespace-nowrap">
                      {log.at
                        ? new Date(log.at).toLocaleString(undefined, {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          })
                        : '—'}
                    </td>
                    <td className="py-2.5 px-3 font-semibold text-slate-900">
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-mono text-slate-700">
                        {log.action}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-[12px] font-mono text-slate-600">
                      {log.actor_user_id || 'system'}
                    </td>
                    <td className="py-2.5 px-3 text-[12px] font-mono text-slate-600">
                      {log.target_user_id || '—'}
                    </td>
                    <td className="py-2.5 px-3 text-[11.5px] text-slate-500 max-w-xs truncate">
                      {log.detail ? JSON.stringify(log.detail) : '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Unified Add Person Modal (From directory + Add manually) */}
      <AddPersonModal
        isOpen={showAddPersonModal}
        onClose={() => setShowAddPersonModal(false)}
        onSuccess={(msg) => setSuccessMessage(msg)}
        onUsersAdded={loadData}
      />

      {/* Reusable Confirmation Dialog for Risky Actions */}
      <ConfirmDialog
        open={confirmState.isOpen}
        title={confirmState.title}
        message={confirmState.message}
        confirmLabel={confirmState.confirmLabel}
        isDestructive={confirmState.isDestructive}
        isLoading={confirmState.isLoading}
        error={confirmState.error}
        onConfirm={handleExecuteConfirm}
        onClose={() => setConfirmState((prev) => ({ ...prev, isOpen: false, error: null }))}
      />

      {/* Reset Password Modal */}
      {resetPasswordTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4 animate-fade-in">
          <div className="w-full max-w-sm rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-[#E9DFC9]/80 pb-3">
              <h3 className="text-[17px] font-bold text-[#1E222D]">Reset Password</h3>
              <button
                type="button"
                onClick={() => setResetPasswordTarget(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <p className="mt-3 text-[13px] text-slate-600">
              Set a temporary password for{' '}
              <strong className="text-slate-900">{resetPasswordTarget.display_name}</strong> (
              {resetPasswordTarget.username}).
            </p>

            <form onSubmit={handleResetPasswordSubmit} className="mt-4 space-y-4 text-[13px]">
              <div>
                <label className="block font-semibold text-[#1E222D]">New Password</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={resetNewPasswordVal}
                  onChange={(e) => setResetNewPasswordVal(e.target.value)}
                  placeholder="At least 8 characters"
                  className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                />
              </div>

              <label className="flex items-center gap-2 font-medium text-slate-700">
                <input
                  type="checkbox"
                  checked={resetMustChange}
                  onChange={(e) => setResetMustChange(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                />
                <span>Require password change on next login</span>
              </label>

              <div className="mt-6 flex justify-end gap-2.5 border-t border-[#E9DFC9]/80 pt-4">
                <button
                  type="button"
                  onClick={() => setResetPasswordTarget(null)}
                  className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmittingReset}
                  className="rounded-xl bg-[#1E222D] px-4 py-2 font-semibold text-[#FFFCF5] hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60"
                >
                  {isSubmittingReset ? 'Resetting...' : 'Set Password'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// Subcomponent for Pending User Approval Row
function PendingUserRow({
  user,
  onApprove,
  onDecline,
}: {
  user: AuthUser;
  onApprove: (u: AuthUser, team: string) => void;
  onDecline: (u: AuthUser) => void;
}) {
  const [selectedTeam, setSelectedTeam] = useState<string>('QShala');

  return (
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div className="font-semibold text-slate-900">{user.display_name}</div>
        <div className="text-[12px] text-slate-500">{user.email}</div>
        <div className="text-[11px] text-slate-400">
          Requested{' '}
          {user.created_at ? new Date(user.created_at).toLocaleDateString() : 'recently'}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <select
          value={selectedTeam}
          onChange={(e) => setSelectedTeam(e.target.value)}
          className="rounded-lg border border-amber-300 bg-white py-1 px-2.5 text-[12.5px] font-medium text-slate-800 shadow-2xs focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-400/20"
        >
          {TEAMS.map((t) => (
            <option key={t} value={t}>
              Team: {t}
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={() => onApprove(user, selectedTeam)}
          className="flex items-center gap-1 rounded-lg bg-emerald-700 px-3 py-1.5 text-[12.5px] font-semibold text-white shadow-2xs hover:bg-emerald-800 focus:outline-none"
        >
          <UserCheck className="h-3.5 w-3.5" />
          <span>Approve</span>
        </button>

        <button
          type="button"
          onClick={() => onDecline(user)}
          className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[12.5px] font-semibold text-slate-600 hover:bg-red-50 hover:text-red-700 focus:outline-none"
        >
          <Ban className="h-3.5 w-3.5" />
          <span>Decline</span>
        </button>
      </div>
    </div>
  );
}
