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
} from 'lucide-react';
import { getDirectoryUsers, createUser, DirectoryUser } from '@/lib/api';

const TEAMS = ['School', 'Corporate', 'QShala', 'Consultant'] as const;

interface DirectoryAddModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUsersAdded: () => Promise<void> | void;
  onOpenManualAdd: () => void;
}

interface PersonResult {
  status: 'success' | 'error';
  message?: string;
}

export default function DirectoryAddModal({
  isOpen,
  onClose,
  onUsersAdded,
  onOpenManualAdd,
}: DirectoryAddModalProps) {
  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  // Search & Selection
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedEmails, setSelectedEmails] = useState<Set<string>>(new Set());
  const [bulkTeam, setBulkTeam] = useState<string>('School');
  const [rowTeams, setRowTeams] = useState<Record<string, string>>({});

  // Submission state
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<Record<string, PersonResult>>({});

  const fetchUsers = useCallback(() => {
    setLoading(true);
    setPermissionError(null);
    setGeneralError(null);
    getDirectoryUsers()
      .then((data) => {
        setUsers(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to fetch directory users';
        if (
          msg.includes('Directory access is not enabled') ||
          msg.includes('User.Read.All')
        ) {
          setPermissionError(msg);
        } else {
          setGeneralError(msg);
        }
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    let mounted = true;

    getDirectoryUsers()
      .then((data) => {
        if (mounted) {
          setUsers(data);
          setLoading(false);
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
            setGeneralError(msg);
          }
          setLoading(false);
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
    onClose();
  };

  // Filtered users matching search term
  const filteredUsers = useMemo(() => {
    if (!searchTerm.trim()) return users;
    const term = searchTerm.toLowerCase();
    return users.filter(
      (u) =>
        u.display_name.toLowerCase().includes(term) ||
        u.email.toLowerCase().includes(term)
    );
  }, [users, searchTerm]);

  // Checkbox toggle
  const toggleSelect = (email: string) => {
    setSelectedEmails((prev) => {
      const next = new Set(prev);
      if (next.has(email)) {
        next.delete(email);
      } else {
        next.add(email);
        // Default team for this row if not yet set
        if (!rowTeams[email]) {
          setRowTeams((r) => ({ ...r, [email]: bulkTeam }));
        }
      }
      return next;
    });
  };

  // Select all / deselect all
  const toggleSelectAll = () => {
    if (selectedEmails.size === filteredUsers.length && filteredUsers.length > 0) {
      setSelectedEmails(new Set());
    } else {
      const allEmails = new Set(filteredUsers.map((u) => u.email));
      setSelectedEmails(allEmails);
      setRowTeams((prev) => {
        const next = { ...prev };
        filteredUsers.forEach((u) => {
          if (!next[u.email]) next[u.email] = bulkTeam;
        });
        return next;
      });
    }
  };

  // Bulk team apply
  const handleApplyBulkTeam = () => {
    setRowTeams((prev) => {
      const next = { ...prev };
      selectedEmails.forEach((email) => {
        next[email] = bulkTeam;
      });
      return next;
    });
  };

  // Row team change
  const handleRowTeamChange = (email: string, team: string) => {
    setRowTeams((prev) => ({ ...prev, [email]: team }));
  };

  // Submit batch add
  const handleAddPeople = async () => {
    if (selectedEmails.size === 0 || submitting) return;

    setSubmitting(true);
    setGeneralError(null);
    const selectedList = users.filter((u) => selectedEmails.has(u.email));
    const newResults: Record<string, PersonResult> = { ...results };
    let successCount = 0;

    for (const person of selectedList) {
      const team = rowTeams[person.email] || bulkTeam;
      try {
        await createUser({
          display_name: person.display_name,
          email: person.email,
          team_id: team,
          provider: 'microsoft',
          role: 'member',
        });
        newResults[person.email] = { status: 'success' };
        successCount++;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Failed to add';
        newResults[person.email] = { status: 'error', message: msg };
      }
    }

    setResults(newResults);
    setSubmitting(false);

    if (successCount > 0) {
      await onUsersAdded();
      // Remove successfully added people from the available list
      setUsers((prev) => prev.filter((u) => newResults[u.email]?.status !== 'success'));
      setSelectedEmails((prev) => {
        const next = new Set(prev);
        Object.entries(newResults).forEach(([email, res]) => {
          if (res.status === 'success') next.delete(email);
        });
        return next;
      });
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-xs">
      <div
        className="flex max-h-[88vh] w-full max-w-2xl flex-col rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] shadow-2xl transition-all"
        role="dialog"
        aria-modal="true"
        aria-labelledby="directory-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#E9DFC9] px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-100 text-amber-800">
              <Building2 className="h-5 w-5" />
            </div>
            <div>
              <h3
                id="directory-modal-title"
                className="text-[16px] font-bold text-[#1E222D]"
              >
                Add from Microsoft 365 Directory
              </h3>
              <p className="text-[12px] text-[#686D7B]">
                Pick colleagues from your organization to provision access
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

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Permission Error Banner (503 / 403) */}
          {permissionError && (
            <div className="rounded-xl border border-amber-300 bg-amber-50/80 p-5 shadow-xs">
              <div className="flex items-start gap-3">
                <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
                <div className="space-y-2">
                  <h4 className="text-[14px] font-bold text-amber-900">
                    Directory Permission Required
                  </h4>
                  <p className="text-[13px] leading-relaxed text-amber-800">
                    {permissionError}
                  </p>
                  <div className="pt-2">
                    <button
                      type="button"
                      onClick={onOpenManualAdd}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#1E222D] px-3.5 py-1.5 text-[12.5px] font-semibold text-[#FFFCF5] shadow-sm transition-colors hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                    >
                      <UserPlus className="h-3.5 w-3.5" />
                      <span>Use manual &quot;Add Person&quot; instead</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* General Error */}
          {generalError && (
            <div className="mb-4 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 p-4 text-[13px] text-red-800">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
                <span>{generalError}</span>
              </div>
              <button
                type="button"
                onClick={fetchUsers}
                className="flex items-center gap-1 text-[12px] font-semibold text-red-700 hover:underline"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                <span>Retry</span>
              </button>
            </div>
          )}

          {/* Controls: Search & Bulk Team */}
          {!permissionError && (
            <div className="space-y-3">
              <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                {/* Search Bar */}
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                  <input
                    type="text"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    placeholder="Search by name or email..."
                    className="w-full rounded-xl border border-[#E9DFC9] bg-white py-2 pl-9 pr-3 text-[13px] text-[#1E222D] transition-all placeholder:text-slate-400 focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
                  />
                  {searchTerm && (
                    <button
                      type="button"
                      onClick={() => setSearchTerm('')}
                      className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>

                {/* Bulk Apply Team */}
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-medium text-[#686D7B] shrink-0">
                    Apply team:
                  </span>
                  <select
                    value={bulkTeam}
                    onChange={(e) => setBulkTeam(e.target.value)}
                    className="rounded-lg border border-[#E9DFC9] bg-white px-2.5 py-1.5 text-[12.5px] font-medium text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
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
                    disabled={selectedEmails.size === 0}
                    className="rounded-lg border border-[#E9DFC9] bg-white px-2.5 py-1.5 text-[12px] font-semibold text-[#1E222D] transition-colors hover:bg-slate-50 disabled:opacity-40"
                    title="Apply selected team to all checked people"
                  >
                    Apply
                  </button>
                </div>
              </div>

              {/* Directory List Container */}
              <div className="overflow-hidden rounded-xl border border-[#E9DFC9] bg-white">
                {/* Header row */}
                <div className="flex items-center justify-between border-b border-[#E9DFC9] bg-[#FBF7EE] px-4 py-2 text-[12px] font-semibold text-[#686D7B]">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={
                        filteredUsers.length > 0 &&
                        selectedEmails.size === filteredUsers.length
                      }
                      onChange={toggleSelectAll}
                      disabled={loading || filteredUsers.length === 0}
                      className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                      aria-label="Select all colleagues"
                    />
                    <span>Colleague ({filteredUsers.length})</span>
                  </div>
                  <div className="pr-2">
                    <span>Assigned Team</span>
                  </div>
                </div>

                {/* List items */}
                <div className="max-h-[320px] divide-y divide-[#E9DFC9]/60 overflow-y-auto">
                  {loading ? (
                    <div className="flex items-center justify-center py-12 text-[13px] text-slate-500">
                      <Loader2 className="mr-2 h-4 w-4 animate-spin text-amber-600" />
                      <span>Loading organization directory...</span>
                    </div>
                  ) : filteredUsers.length === 0 ? (
                    <div className="py-10 text-center text-[13px] text-slate-500">
                      {searchTerm
                        ? `No colleagues match "${searchTerm}"`
                        : 'No new colleagues found in directory.'}
                    </div>
                  ) : (
                    filteredUsers.map((person) => {
                      const isSelected = selectedEmails.has(person.email);
                      const res = results[person.email];
                      const assignedTeam = rowTeams[person.email] || bulkTeam;

                      return (
                        <div
                          key={person.email}
                          className={`flex items-center justify-between px-4 py-2.5 transition-colors ${
                            isSelected ? 'bg-amber-50/40' : 'hover:bg-slate-50/60'
                          }`}
                        >
                          <label className="flex flex-1 cursor-pointer items-center gap-3 pr-2">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleSelect(person.email)}
                              className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                            />
                            <div className="min-w-0">
                              <p className="truncate text-[13.5px] font-semibold text-[#1E222D]">
                                {person.display_name}
                              </p>
                              <p className="truncate text-[11.5px] text-[#686D7B]">
                                {person.email}
                              </p>
                            </div>
                          </label>

                          <div className="flex items-center gap-2.5">
                            {/* Outcome badge */}
                            {res?.status === 'success' && (
                              <span className="flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-700">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                <span>Added</span>
                              </span>
                            )}
                            {res?.status === 'error' && (
                              <span
                                className="flex items-center gap-1 rounded-md bg-red-50 px-2 py-0.5 text-[11.5px] font-semibold text-red-700"
                                title={res.message}
                              >
                                <AlertTriangle className="h-3.5 w-3.5" />
                                <span>Failed</span>
                              </span>
                            )}

                            {/* Team Select */}
                            <select
                              value={assignedTeam}
                              onChange={(e) =>
                                handleRowTeamChange(person.email, e.target.value)
                              }
                              className="rounded-lg border border-[#E9DFC9] bg-white px-2 py-1 text-[12px] font-medium text-[#1E222D] focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-400/20"
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
                    })
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[#E9DFC9] bg-[#FBF7EE] px-6 py-3.5">
          <div className="text-[12.5px] text-[#686D7B]">
            {selectedEmails.size > 0 ? (
              <span>
                <strong className="text-[#1E222D]">{selectedEmails.size}</strong>{' '}
                {selectedEmails.size === 1 ? 'person' : 'people'} selected
              </span>
            ) : (
              <span>Select colleagues to add</span>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleAddPeople}
              disabled={selectedEmails.size === 0 || submitting || loading}
              className="flex items-center gap-2 rounded-xl bg-[#1E222D] px-4 py-2 text-[13px] font-semibold text-[#FFFCF5] shadow-sm transition-all hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-40"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
                  <span>Adding...</span>
                </>
              ) : (
                <>
                  <UserPlus className="h-4 w-4 text-amber-400" />
                  <span>
                    Add {selectedEmails.size}{' '}
                    {selectedEmails.size === 1 ? 'person' : 'people'}
                  </span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
