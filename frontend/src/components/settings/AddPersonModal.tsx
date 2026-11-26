'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  X,
  Search,
  Building2,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  UserPlus,
  ShieldAlert,
  Loader2,
  KeyRound,
} from 'lucide-react';
import { getDirectoryUsers, createUser, DirectoryUser, AuthUser } from '@/lib/api';

const TEAMS = ['School', 'Corporate', 'QShala', 'Consultant'] as const;

interface AddPersonModalProps {
  isOpen: boolean;
  initialTab?: 'directory' | 'manual';
  onClose: () => void;
  onSuccess: (message: string) => void;
  onUsersAdded: () => Promise<void> | void;
}

interface PersonResult {
  status: 'success' | 'error';
  message?: string;
  restored?: boolean;
}

export default function AddPersonModal({
  isOpen,
  initialTab = 'directory',
  onClose,
  onSuccess,
  onUsersAdded,
}: AddPersonModalProps) {
  const [activeTab, setActiveTab] = useState<'directory' | 'manual'>(initialTab);

  useEffect(() => {
    if (isOpen && initialTab) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  // ------------------------------------------------ Directory Tab State
  const [directoryUsers, setDirectoryUsers] = useState<DirectoryUser[]>([]);
  const [loadingDirectory, setLoadingDirectory] = useState(true);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [directoryGeneralError, setDirectoryGeneralError] = useState<string | null>(null);

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedEmails, setSelectedEmails] = useState<Set<string>>(new Set());
  const [bulkTeam, setBulkTeam] = useState<string>('School');
  const [rowTeams, setRowTeams] = useState<Record<string, string>>({});
  const [submittingDirectory, setSubmittingDirectory] = useState(false);
  const [results, setResults] = useState<Record<string, PersonResult>>({});

  // ------------------------------------------------ Manual Tab State
  const [signInType, setSignInType] = useState<'microsoft' | 'password'>('microsoft');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [team, setTeam] = useState<string>('QShala');
  const [role, setRole] = useState<'admin' | 'member'>('member');
  const [mustChangePassword, setMustChangePassword] = useState(true);
  const [submittingManual, setSubmittingManual] = useState(false);
  const [manualError, setManualError] = useState<string | null>(null);

  const fetchDirectory = useCallback(() => {
    setLoadingDirectory(true);
    setPermissionError(null);
    setDirectoryGeneralError(null);
    getDirectoryUsers()
      .then((data) => {
        setDirectoryUsers(data);
        setLoadingDirectory(false);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to fetch directory users';
        if (
          msg.includes('Directory access is not enabled') ||
          msg.includes('User.Read.All')
        ) {
          setPermissionError(msg);
        } else {
          setDirectoryGeneralError(msg);
        }
        setLoadingDirectory(false);
      });
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    let mounted = true;
    getDirectoryUsers()
      .then((data) => {
        if (mounted) {
          setDirectoryUsers(data);
          setLoadingDirectory(false);
        }
      })
      .catch((err: unknown) => {
        if (mounted) {
          const msg = err instanceof Error ? err.message : 'Failed to fetch directory users';
          if (
            msg.includes('Directory access is not enabled') ||
            msg.includes('User.Read.All')
          ) {
            setPermissionError(msg);
          } else {
            setDirectoryGeneralError(msg);
          }
          setLoadingDirectory(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, [isOpen]);

  const handleClose = () => {
    setSearchTerm('');
    setSelectedEmails(new Set());
    setRowTeams({});
    setResults({});
    setDisplayName('');
    setEmail('');
    setUsername('');
    setPassword('');
    setManualError(null);
    setActiveTab('directory');
    onClose();
  };

  // ------------------------------------------------ Directory Handlers
  const filteredDirectoryUsers = useMemo(() => {
    if (!searchTerm.trim()) return directoryUsers;
    const term = searchTerm.toLowerCase();
    return directoryUsers.filter(
      (u) =>
        u.display_name.toLowerCase().includes(term) ||
        u.email.toLowerCase().includes(term)
    );
  }, [directoryUsers, searchTerm]);

  const toggleSelect = (emailVal: string) => {
    setSelectedEmails((prev) => {
      const next = new Set(prev);
      if (next.has(emailVal)) {
        next.delete(emailVal);
      } else {
        next.add(emailVal);
        if (!rowTeams[emailVal]) {
          setRowTeams((r) => ({ ...r, [emailVal]: bulkTeam }));
        }
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedEmails.size === filteredDirectoryUsers.length && filteredDirectoryUsers.length > 0) {
      setSelectedEmails(new Set());
    } else {
      const allEmails = new Set(filteredDirectoryUsers.map((u) => u.email));
      setSelectedEmails(allEmails);
      setRowTeams((prev) => {
        const next = { ...prev };
        filteredDirectoryUsers.forEach((u) => {
          if (!next[u.email]) next[u.email] = bulkTeam;
        });
        return next;
      });
    }
  };

  const handleApplyBulkTeam = () => {
    setRowTeams((prev) => {
      const next = { ...prev };
      selectedEmails.forEach((emailVal) => {
        next[emailVal] = bulkTeam;
      });
      return next;
    });
  };

  const handleRowTeamChange = (emailVal: string, teamVal: string) => {
    setRowTeams((prev) => ({ ...prev, [emailVal]: teamVal }));
  };

  const handleAddDirectoryPeople = async () => {
    if (selectedEmails.size === 0 || submittingDirectory) return;

    setSubmittingDirectory(true);
    setDirectoryGeneralError(null);
    const selectedList = directoryUsers.filter((u) => selectedEmails.has(u.email));
    const newResults: Record<string, PersonResult> = { ...results };
    let successCount = 0;
    const restoredNames: string[] = [];

    for (const person of selectedList) {
      const selectedTeam = rowTeams[person.email] || bulkTeam;
      try {
        const createdUser: AuthUser = await createUser({
          display_name: person.display_name,
          email: person.email,
          team_id: selectedTeam,
          provider: 'microsoft',
          role: 'member',
        });
        const isRestored = Boolean(createdUser.restored);
        newResults[person.email] = { status: 'success', restored: isRestored };
        successCount++;
        if (isRestored) {
          restoredNames.push(person.display_name);
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Failed to add';
        newResults[person.email] = { status: 'error', message: msg };
      }
    }

    setResults(newResults);
    setSubmittingDirectory(false);

    if (successCount > 0) {
      await onUsersAdded();
      setDirectoryUsers((prev) => prev.filter((u) => newResults[u.email]?.status !== 'success'));
      setSelectedEmails((prev) => {
        const next = new Set(prev);
        Object.entries(newResults).forEach(([em, res]) => {
          if (res.status === 'success') next.delete(em);
        });
        return next;
      });

      if (restoredNames.length > 0) {
        onSuccess(
          restoredNames.length === 1
            ? `${restoredNames[0]}'s account has been restored`
            : `${restoredNames.join(', ')} accounts have been restored`
        );
      } else {
        onSuccess(`Successfully added ${successCount} person${successCount > 1 ? 's' : ''}`);
      }
    }
  };

  // ------------------------------------------------ Manual Form Handler
  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittingManual(true);
    setManualError(null);
    try {
      const createdUser: AuthUser = await createUser({
        display_name: displayName.trim(),
        provider: signInType,
        email: signInType === 'microsoft' ? email.trim().toLowerCase() : undefined,
        username: signInType === 'password' ? username.trim() : undefined,
        password: signInType === 'password' ? password : undefined,
        team_id: team,
        role: signInType === 'microsoft' ? role : 'member',
        must_change_password: signInType === 'password' ? mustChangePassword : false,
      });

      if (createdUser.restored) {
        onSuccess(`${displayName.trim()}'s account has been restored`);
      } else {
        onSuccess(`Successfully created user ${displayName.trim()}`);
      }

      handleClose();
      await onUsersAdded();
    } catch (err: unknown) {
      setManualError(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setSubmittingManual(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 p-4 backdrop-blur-xs animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-person-modal-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] shadow-2xl transition-all">
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-[#E9DFC9] px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 text-amber-800">
              <UserPlus className="h-5 w-5" />
            </div>
            <div>
              <h3 id="add-person-modal-title" className="text-[17px] font-bold text-[#1E222D]">
                Add Person
              </h3>
              <p className="text-[12.5px] text-[#686D7B]">
                Provision access for team members and external consultants
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus:outline-none"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab Selector */}
        <div className="border-b border-[#E9DFC9] bg-[#FAF5EC]/60 px-6 pt-3">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setActiveTab('directory')}
              className={`flex items-center gap-2 border-b-2 px-4 py-2 text-[13px] font-semibold transition-all ${
                activeTab === 'directory'
                  ? 'border-amber-500 text-amber-900'
                  : 'border-transparent text-slate-600 hover:text-slate-900'
              }`}
            >
              <Building2 className="h-4 w-4" />
              <span>From directory</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('manual')}
              className={`flex items-center gap-2 border-b-2 px-4 py-2 text-[13px] font-semibold transition-all ${
                activeTab === 'manual'
                  ? 'border-amber-500 text-amber-900'
                  : 'border-transparent text-slate-600 hover:text-slate-900'
              }`}
            >
              <UserPlus className="h-4 w-4" />
              <span>Add manually</span>
            </button>
          </div>
        </div>

        {/* Modal Content Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* TAB 1: FROM DIRECTORY */}
          {activeTab === 'directory' && (
            <div className="space-y-4">
              {/* Permission Error Banner (503 / 403) */}
              {permissionError && (
                <div className="rounded-xl border border-amber-300 bg-amber-50/80 p-4">
                  <div className="flex items-start gap-3">
                    <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
                    <div className="space-y-1 text-[13px] text-amber-900">
                      <div className="font-bold">Directory Search Unavailable</div>
                      <p className="leading-relaxed text-amber-800">{permissionError}</p>
                      <p className="text-[12px] text-amber-700/90">
                        Ask an administrator to grant <code className="font-mono bg-amber-100/80 px-1 py-0.5 rounded text-[11.5px]">User.Read.All</code> application permission on the Azure login app registration.
                      </p>
                      <div className="pt-2">
                        <button
                          type="button"
                          onClick={() => setActiveTab('manual')}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-700 px-3 py-1.5 text-[12.5px] font-semibold text-white hover:bg-amber-800 shadow-2xs"
                        >
                          <UserPlus className="h-3.5 w-3.5" />
                          <span>Switch to Add manually</span>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* General Error Banner */}
              {directoryGeneralError && (
                <div className="flex items-center justify-between rounded-xl border border-red-200 bg-red-50 p-3.5 text-[13px] text-red-800">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
                    <span>{directoryGeneralError}</span>
                  </div>
                  <button
                    type="button"
                    onClick={fetchDirectory}
                    className="flex items-center gap-1 font-semibold text-red-700 hover:text-red-900 underline"
                  >
                    <RefreshCw className="h-3 w-3" />
                    <span>Retry</span>
                  </button>
                </div>
              )}

              {/* Search & Bulk Team Toolbar */}
              {!permissionError && (
                <div className="space-y-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                    <input
                      type="text"
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                      placeholder="Search colleagues by name or email..."
                      className="w-full rounded-xl border border-[#E9DFC9] bg-white pl-9 pr-4 py-2 text-[13px] text-[#1E222D] placeholder-slate-400 focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>

                  {/* Bulk Team Toolbar */}
                  {selectedEmails.size > 0 && (
                    <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-xl border border-amber-300 bg-amber-50/70 p-3">
                      <div className="text-[12.5px] font-semibold text-amber-900">
                        {selectedEmails.size} person{selectedEmails.size > 1 ? 's' : ''} selected
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[12px] text-amber-800">Assign team:</span>
                        <select
                          value={bulkTeam}
                          onChange={(e) => setBulkTeam(e.target.value)}
                          className="rounded-lg border border-amber-300 bg-white py-1 px-2.5 text-[12.5px] font-medium text-slate-800 shadow-2xs focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-400/20"
                        >
                          {TEAMS.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={handleApplyBulkTeam}
                          className="rounded-lg border border-amber-400 bg-white px-2.5 py-1 text-[12px] font-semibold text-amber-900 hover:bg-amber-100/50 shadow-2xs"
                        >
                          Apply to all
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Directory User List */}
              <div className="rounded-xl border border-[#E9DFC9] bg-white overflow-hidden shadow-2xs">
                {loadingDirectory ? (
                  <div className="py-12 text-center text-[13px] text-slate-400">
                    <Loader2 className="mx-auto mb-2 h-6 w-6 animate-spin text-amber-500" />
                    Loading Microsoft 365 organization directory...
                  </div>
                ) : filteredDirectoryUsers.length === 0 ? (
                  <div className="py-10 text-center text-[13px] text-slate-500 px-4">
                    {searchTerm ? (
                      <div>No colleagues found matching &quot;{searchTerm}&quot;.</div>
                    ) : (
                      <div>
                        All organization members already have accounts or no eligible colleagues found.
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => setActiveTab('manual')}
                      className="mt-3 inline-flex items-center gap-1.5 font-semibold text-amber-700 hover:text-amber-900 underline text-[12.5px]"
                    >
                      <UserPlus className="h-3.5 w-3.5" />
                      <span>Add manually instead</span>
                    </button>
                  </div>
                ) : (
                  <div className="divide-y divide-slate-100 max-h-[320px] overflow-y-auto">
                    {/* Header bar with select all */}
                    <div className="flex items-center justify-between bg-slate-50/80 px-4 py-2 text-[11.5px] font-bold uppercase tracking-wider text-slate-500">
                      <label className="flex items-center gap-2.5 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={
                            selectedEmails.size === filteredDirectoryUsers.length &&
                            filteredDirectoryUsers.length > 0
                          }
                          onChange={toggleSelectAll}
                          className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                        />
                        <span>Select All ({filteredDirectoryUsers.length})</span>
                      </label>
                      <span>Team Assignment</span>
                    </div>

                    {filteredDirectoryUsers.map((person) => {
                      const isSelected = selectedEmails.has(person.email);
                      const rowTeam = rowTeams[person.email] || bulkTeam;
                      const res = results[person.email];

                      return (
                        <div
                          key={person.email}
                          className={`flex items-center justify-between gap-3 px-4 py-2.5 transition-colors ${
                            isSelected ? 'bg-amber-50/40' : 'hover:bg-slate-50/70'
                          }`}
                        >
                          <label className="flex items-center gap-3 cursor-pointer min-w-0 flex-1">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleSelect(person.email)}
                              className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                            />
                            <div className="min-w-0 flex-1">
                              <div className="font-semibold text-slate-900 text-[13px] truncate">
                                {person.display_name}
                              </div>
                              <div className="text-[11.5px] text-slate-500 truncate">
                                {person.email}
                              </div>
                            </div>
                          </label>

                          <div className="flex items-center gap-2">
                            {res?.status === 'error' && (
                              <span
                                className="text-[11px] font-medium text-red-600 max-w-[140px] truncate"
                                title={res.message}
                              >
                                {res.message}
                              </span>
                            )}
                            {res?.status === 'success' && (
                              <span className="flex items-center gap-1 text-[11.5px] font-semibold text-emerald-700">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                {res.restored ? 'Restored' : 'Added'}
                              </span>
                            )}

                            <select
                              value={rowTeam}
                              onChange={(e) => handleRowTeamChange(person.email, e.target.value)}
                              disabled={!isSelected || submittingDirectory}
                              className="rounded-lg border border-slate-200 bg-white py-1 px-2 text-[12px] font-medium text-slate-800 shadow-2xs focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-400/20 disabled:opacity-40"
                            >
                              {TEAMS.map((t) => (
                                <option key={t} value={t}>
                                  {t}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 2: ADD MANUALLY */}
          {activeTab === 'manual' && (
            <form onSubmit={handleManualSubmit} className="space-y-4 text-[13px]">
              {manualError && (
                <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-3.5 text-[13px] text-red-800">
                  <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
                  <span>{manualError}</span>
                </div>
              )}

              {/* Sign-in Type Selection */}
              <div>
                <label className="block font-semibold text-[#1E222D]">Sign-in Type</label>
                <div className="mt-1.5 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSignInType('microsoft');
                      setRole('member');
                    }}
                    className={`flex items-center justify-center gap-2 rounded-xl border py-2.5 text-center font-semibold transition-all ${
                      signInType === 'microsoft'
                        ? 'border-amber-400 bg-amber-50 text-amber-900 ring-2 ring-amber-400/20'
                        : 'border-[#E9DFC9] bg-white text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    <Building2 className="h-4 w-4 text-blue-600" />
                    <span>Microsoft account</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setSignInType('password');
                      setRole('member');
                    }}
                    className={`flex items-center justify-center gap-2 rounded-xl border py-2.5 text-center font-semibold transition-all ${
                      signInType === 'password'
                        ? 'border-amber-400 bg-amber-50 text-amber-900 ring-2 ring-amber-400/20'
                        : 'border-[#E9DFC9] bg-white text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    <KeyRound className="h-4 w-4 text-amber-600" />
                    <span>Username and password</span>
                  </button>
                </div>
              </div>

              {/* Full Display Name */}
              <div>
                <label className="block font-semibold text-[#1E222D]">Full Display Name</label>
                <input
                  type="text"
                  required
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="e.g. Priya Sharma"
                  className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                />
              </div>

              {/* Conditional Fields: Microsoft vs Password */}
              {signInType === 'microsoft' ? (
                <>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">
                      Organization Email (@walnuts.co.in)
                    </label>
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="priya@walnuts.co.in"
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">Role</label>
                    <select
                      value={role}
                      onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Administrator</option>
                    </select>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">Username</label>
                    <input
                      type="text"
                      required
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="e.g. priya_consultant"
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>
                  <div>
                    <label className="block font-semibold text-[#1E222D]">Initial Password</label>
                    <input
                      type="password"
                      required
                      minLength={8}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="At least 8 characters"
                      className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    />
                  </div>
                  <label className="flex items-center gap-2 font-medium text-slate-700">
                    <input
                      type="checkbox"
                      checked={mustChangePassword}
                      onChange={(e) => setMustChangePassword(e.target.checked)}
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
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-[#E9DFC9] bg-white px-3 py-2 text-[13.5px] text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                >
                  {TEAMS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>

              {/* Manual Form Submit button */}
              <div className="mt-6 flex justify-end gap-2.5 border-t border-[#E9DFC9]/80 pt-4">
                <button
                  type="button"
                  onClick={handleClose}
                  className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingManual}
                  className="rounded-xl bg-[#1E222D] px-4 py-2 font-semibold text-[#FFFCF5] hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60 flex items-center gap-2"
                >
                  {submittingManual && <Loader2 className="h-4 w-4 animate-spin" />}
                  <span>{submittingManual ? 'Creating...' : 'Create Person'}</span>
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Directory Tab Footer */}
        {activeTab === 'directory' && (
          <div className="flex items-center justify-between border-t border-[#E9DFC9] px-6 py-4 bg-[#FAF5EC]/40">
            <div className="text-[12.5px] text-[#686D7B]">
              Colleagues receive standard member role with Microsoft SSO
            </div>
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleAddDirectoryPeople}
                disabled={selectedEmails.size === 0 || submittingDirectory}
                className="flex items-center gap-2 rounded-xl bg-[#1E222D] px-4 py-2 text-[13px] font-semibold text-[#FFFCF5] hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-50"
              >
                {submittingDirectory ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>Adding...</span>
                  </>
                ) : (
                  <>
                    <UserPlus className="h-4 w-4" />
                    <span>
                      Add {selectedEmails.size > 0 ? `${selectedEmails.size} ` : ''}Person
                      {selectedEmails.size > 1 ? 's' : ''}
                    </span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
