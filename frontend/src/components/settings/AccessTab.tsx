'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  AuthUser,
  AuditLogEntry,
  listUsers,
  createUser,
  updateUser,
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
  Building2,
} from 'lucide-react';
import DirectoryAddModal from './DirectoryAddModal';

const TEAMS = ['School', 'Corporate', 'QShala', 'Consultant'] as const;

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
  const [showAddUserModal, setShowAddUserModal] = useState(false);
  const [showDirectoryModal, setShowDirectoryModal] = useState(false);
  const [resetPasswordTarget, setResetPasswordTarget] = useState<AuthUser | null>(null);

  // Add User Form State
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newProvider, setNewProvider] = useState<'microsoft' | 'password'>('microsoft');
  const [newEmail, setNewEmail] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newTeam, setNewTeam] = useState<string>('QShala');
  const [newRole, setNewRole] = useState<'admin' | 'member'>('member');
  const [newMustChangePassword, setNewMustChangePassword] = useState(true);
  const [isSubmittingNewUser, setIsSubmittingNewUser] = useState(false);

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

  // Handle Role Change (Admin <-> Member)
  const handleRoleToggle = async (targetUser: AuthUser) => {
    if (targetUser.provider !== 'microsoft') {
      setErrorMessage('Only Microsoft provider accounts can be granted Admin privileges.');
      return;
    }
    const newRole = targetUser.role === 'admin' ? 'member' : 'admin';
    if (targetUser.role === 'admin' && activeAdminsCount <= 1) {
      setErrorMessage('Cannot remove the last active administrator.');
      return;
    }
    try {
      setErrorMessage(null);
      await updateUser(targetUser.id, { role: newRole });
      setSuccessMessage(`Updated ${targetUser.display_name} to ${newRole}`);
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update role');
    }
  };

  // Handle Status Change (Block <-> Active)
  const handleStatusToggle = async (targetUser: AuthUser) => {
    if (targetUser.role === 'admin' && targetUser.status === 'active' && activeAdminsCount <= 1) {
      setErrorMessage('Cannot block the last active administrator.');
      return;
    }
    const nextStatus = targetUser.status === 'active' ? 'blocked' : 'active';
    try {
      setErrorMessage(null);
      await updateUser(targetUser.id, { status: nextStatus });
      setSuccessMessage(
        `${nextStatus === 'active' ? 'Unblocked' : 'Blocked'} ${targetUser.display_name}`
      );
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to change user status');
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

  // Handle Create User Submit
  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmittingNewUser(true);
    setErrorMessage(null);
    try {
      await createUser({
        display_name: newDisplayName.trim(),
        provider: newProvider,
        email: newProvider === 'microsoft' ? newEmail.trim().toLowerCase() : undefined,
        username: newProvider === 'password' ? newUsername.trim() : undefined,
        password: newProvider === 'password' ? newPassword : undefined,
        team_id: newTeam,
        role: newProvider === 'microsoft' ? newRole : 'member',
        must_change_password: newProvider === 'password' ? newMustChangePassword : false,
      });
      setSuccessMessage(`Successfully created user ${newDisplayName}`);
      setShowAddUserModal(false);
      setNewDisplayName('');
      setNewEmail('');
      setNewUsername('');
      setNewPassword('');
      await loadData();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setIsSubmittingNewUser(false);
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
  const activeAndBlockedUsers = users.filter((u) => u.status !== 'pending');

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
                onBlock={handleStatusToggle}
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

            <button
              type="button"
              onClick={() => setShowDirectoryModal(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[#E9DFC9] bg-[#FFFCF5] px-3.5 py-1.5 text-[12.5px] font-semibold text-[#1E222D] shadow-xs transition-colors hover:bg-slate-50 focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
            >
              <Building2 className="h-3.5 w-3.5 text-amber-600" />
              <span>Add from directory</span>
            </button>

            <button
              type="button"
              onClick={() => setShowAddUserModal(true)}
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
                          {/* Toggle Admin (Microsoft only) */}
                          {u.provider === 'microsoft' && (
                            <button
                              type="button"
                              onClick={() => handleRoleToggle(u)}
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

                          {/* Block / Unblock Toggle */}
                          <button
                            type="button"
                            onClick={() => handleStatusToggle(u)}
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

      {/* Add User Modal */}
      {showAddUserModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-[#E9DFC9]/80 pb-3">
              <h3 className="text-[17px] font-bold text-[#1E222D]">Add New Person</h3>
              <button
                type="button"
                onClick={() => setShowAddUserModal(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <form onSubmit={handleCreateUser} className="mt-4 space-y-4 text-[13px]">
              {/* Provider Selection */}
              <div>
                <label className="block font-semibold text-[#1E222D]">Account Type</label>
                <div className="mt-1.5 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setNewProvider('microsoft');
                      setNewRole('member');
                    }}
                    className={`rounded-xl border py-2 text-center font-semibold transition-all ${
                      newProvider === 'microsoft'
                        ? 'border-amber-400 bg-amber-50 text-amber-900 ring-2 ring-amber-400/20'
                        : 'border-[#E9DFC9] bg-white text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    Microsoft SSO
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNewProvider('password');
                      setNewRole('member');
                    }}
                    className={`rounded-xl border py-2 text-center font-semibold transition-all ${
                      newProvider === 'password'
                        ? 'border-amber-400 bg-amber-50 text-amber-900 ring-2 ring-amber-400/20'
                        : 'border-[#E9DFC9] bg-white text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    Password
                  </button>
                </div>
              </div>

              {/* Display Name */}
              <div>
                <label className="block font-semibold text-[#1E222D]">Full Display Name</label>
                <input
                  type="text"
                  required
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                  placeholder="e.g. Priya Sharma"
                  className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                />
              </div>

              {/* Microsoft Email or Password Username */}
              {newProvider === 'microsoft' ? (
                <div>
                  <label className="block font-semibold text-[#1E222D]">
                    Organization Email (@walnuts.co.in)
                  </label>
                  <input
                    type="email"
                    required
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder="priya@walnuts.co.in"
                    className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                  />
                </div>
              ) : (
                <>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">Username</label>
                    <input
                      type="text"
                      required
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      placeholder="e.g. priya_consultant"
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">Starting Password</label>
                    <input
                      type="password"
                      required
                      minLength={8}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="At least 8 characters"
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>
                  <label className="flex items-center gap-2 font-medium text-slate-700">
                    <input
                      type="checkbox"
                      checked={newMustChangePassword}
                      onChange={(e) => setNewMustChangePassword(e.target.checked)}
                      className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                    />
                    <span>Must change password on first login</span>
                  </label>
                </>
              )}

              {/* Team Selection */}
              <div>
                <label className="block font-semibold text-[#1E222D]">Team</label>
                <select
                  value={newTeam}
                  onChange={(e) => setNewTeam(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                >
                  {TEAMS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>

              {/* Role (Microsoft only) */}
              {newProvider === 'microsoft' && (
                <div>
                  <label className="block font-semibold text-[#1E222D]">Role</label>
                  <select
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value as 'admin' | 'member')}
                    className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                  >
                    <option value="member">Member</option>
                    <option value="admin">Administrator</option>
                  </select>
                </div>
              )}

              <div className="mt-6 flex justify-end gap-2.5 border-t border-[#E9DFC9]/80 pt-4">
                <button
                  type="button"
                  onClick={() => setShowAddUserModal(false)}
                  className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmittingNewUser}
                  className="rounded-xl bg-[#1E222D] px-4 py-2 font-semibold text-[#FFFCF5] hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60"
                >
                  {isSubmittingNewUser ? 'Creating...' : 'Create Person'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Reset Password Modal */}
      {resetPasswordTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
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

      {/* Microsoft 365 Directory Add Modal */}
      <DirectoryAddModal
        isOpen={showDirectoryModal}
        onClose={() => setShowDirectoryModal(false)}
        onUsersAdded={loadData}
        onOpenManualAdd={() => {
          setShowDirectoryModal(false);
          setShowAddUserModal(true);
        }}
      />
    </div>
  );
}

// Subcomponent for Pending User Approval Row
function PendingUserRow({
  user,
  onApprove,
  onBlock,
}: {
  user: AuthUser;
  onApprove: (u: AuthUser, team: string) => void;
  onBlock: (u: AuthUser) => void;
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
          onClick={() => onBlock(user)}
          className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[12.5px] font-semibold text-slate-600 hover:bg-red-50 hover:text-red-700 focus:outline-none"
        >
          <Ban className="h-3.5 w-3.5" />
          <span>Decline</span>
        </button>
      </div>
    </div>
  );
}
