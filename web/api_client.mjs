/** Frontend integration boundary. Import from a future workstation page/component. */
export class StationApi {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.token = ''; // In-memory for this prototype; no embedded administrator credentials.
  }

  async request(method, path, data, csv = false) {
    const headers = { Accept: csv ? 'text/csv' : 'application/json' };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (data !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(this.baseUrl + path, {
      method, headers, body: data === undefined ? undefined : JSON.stringify(data),
    });
    if (!response.ok) {
      const failure = await response.json();
      const error = new Error(failure.error?.message || `HTTP ${response.status}`);
      error.status = response.status;
      error.requestId = failure.request_id;
      throw error;
    }
    if (csv) return response.text();
    return (await response.json()).data;
  }

  async login(username, password) {
    const session = await this.request('POST', '/api/auth/login', { username, password });
    this.token = session.access_token;
    return session.user;
  }

  query(path, filters = {}) {
    const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== '' && v != null));
    return `${path}${params.size ? `?${params}` : ''}`;
  }

  receive(data) { return this.request('POST', '/api/parcels', data); }
  search(filters) { return this.request('GET', this.query('/api/parcels', filters)); }
  get(id) { return this.request('GET', `/api/parcels/${id}`); }
  pickup(id, recipient, pickupCode) {
    return this.request('POST', `/api/parcels/${id}/pickup`, { recipient, pickup_code: pickupCode });
  }
  relocate(id, location) { return this.request('PATCH', `/api/parcels/${id}/location`, { location }); }
  markException(id, kind, note) { return this.request('POST', `/api/parcels/${id}/exceptions`, { kind, note }); }
  resolveException(id, resolution, note) { return this.request('POST', `/api/exceptions/${id}/resolve`, { resolution, note }); }
  resetPickup(id) { return this.request('POST', `/api/parcels/${id}/reset-pickup`, {}); }
  overdue(filters) { return this.request('GET', this.query('/api/parcels/overdue', filters)); }
  statistics() { return this.request('GET', '/api/statistics'); }
  audits(filters) { return this.request('GET', this.query('/api/audits', filters)); }
  exportCsv(filters) { return this.request('GET', this.query('/api/parcels/export.csv', filters), undefined, true); }
  createUser(username, password, role) { return this.request('POST', '/api/users', { username, password, role }); }
  notifications(filters) { return this.request('GET', this.query('/api/notifications', filters)); }
  dispatchNotifications(limit = 20) { return this.request('POST', '/api/notifications/dispatch', { limit }); }
}
