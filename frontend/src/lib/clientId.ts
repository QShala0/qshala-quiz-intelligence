// A random id each browser keeps for itself. The uploads page sends it so "resume my upload" finds this browser's batch
// and never a colleague's. It is not a login and carries no identity.
const KEY = 'qshala_client_id';
let memoryId: string | null = null;

export function getClientId(): string {
  if (memoryId) return memoryId;
  try {
    const existing = localStorage.getItem(KEY);
    if (existing) return (memoryId = existing);
  } catch {
    // storage blocked: fall through to a per-tab id
  }
  const id =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  memoryId = id;
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // ignore
  }
  return id;
}
