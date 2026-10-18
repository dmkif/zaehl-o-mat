/** Per-property permissions derived from the API's `my_role` (spec 014). Display only: the server decides. */

export type PropertyRole = 'admin' | 'manager' | 'user'

export interface PropertyPerms {
  /** create / edit: property admin or manager (global admins arrive as "admin") */
  canWrite: boolean
  /** delete: property admin */
  canDelete: boolean
}

export function permsFor(role: string | null | undefined): PropertyPerms {
  return {
    canWrite: role === 'admin' || role === 'manager',
    canDelete: role === 'admin',
  }
}
