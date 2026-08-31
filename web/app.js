'use strict';

/* ------------------------------------------------------------------ *
 * Small DOM helper. h('div.klass', {attrs}, ...children)
 * ------------------------------------------------------------------ */
function h(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  if (attrs && (attrs.nodeType || Array.isArray(attrs) || typeof attrs !== 'object')) {
    children.unshift(attrs);
  } else if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className += ' ' + v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') node.value = v;
      else if (k === 'checked' || k === 'disabled' || k === 'selected') node[k] = !!v;
      else node.setAttribute(k, v);
    }
  }
  const add = (c) => {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else node.append(c.nodeType ? c : document.createTextNode(String(c)));
  };
  children.forEach(add);
  return node;
}

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ *
 * State
 * ------------------------------------------------------------------ */
const state = {
  config: null,
  dirty: false,
  tab: 'grid',
  summary: null,      // last solve summary
  issues: [],
  viewKind: 'division',
  viewId: null,
  view: null,
  busy: false,
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* keep */ }
    throw new Error(detail);
  }
  return res.json();
}

function markDirty() {
  state.dirty = true;
  $('#save-state').textContent = 'unsaved changes';
}

/* ------------------------------------------------------------------ *
 * Option lists derived from the config
 * ------------------------------------------------------------------ */
const opts = {
  days: () => state.config.days.map((d) => ({ value: d, label: d })),
  teachingPeriods: () => state.config.periods.filter((p) => p.kind === 'teaching'),
  subjects: () => state.config.subjects.map((s) => ({ value: s.id, label: s.short || s.name || s.id })),
  divisions: () => state.config.divisions.map((d) => ({ value: d.id, label: d.id })),
  batches: () => state.config.batches.map((b) => ({ value: b.id, label: b.id })),
  rooms: () => state.config.rooms.map((r) => ({ value: r.id, label: `${r.id} — ${r.name || r.type}` })),
  teachers: () => state.config.teachers.map((t) => ({ value: t.id, label: t.name || t.id })),
  roomTypes: () => {
    const set = new Set(state.config.rooms.map((r) => r.type));
    state.config.subjects.forEach((s) => set.add(s.room_type));
    return [...set].sort().map((t) => ({ value: t, label: t }));
  },
};

/* ------------------------------------------------------------------ *
 * Cell editors
 * ------------------------------------------------------------------ */
function textCell(obj, key, placeholder) {
  return h('input', {
    value: obj[key] ?? '', placeholder: placeholder || '',
    oninput: (e) => { obj[key] = e.target.value; markDirty(); },
  });
}

function numberCell(obj, key, { min = 0, max = 999, nullable = false } = {}) {
  return h('input', {
    type: 'number', min, max,
    value: obj[key] ?? '',
    placeholder: nullable ? '—' : '',
    oninput: (e) => {
      const raw = e.target.value;
      obj[key] = raw === '' ? (nullable ? null : 0) : Number(raw);
      markDirty();
    },
  });
}

function selectCell(obj, key, options, { blank = null } = {}) {
  const list = typeof options === 'function' ? options() : options;
  const select = h('select', {
    onchange: (e) => { obj[key] = e.target.value || null; markDirty(); },
  });
  if (blank !== null) select.append(h('option', { value: '' }, blank));
  list.forEach((o) => {
    const value = o.value ?? o;
    select.append(h('option', { value, selected: obj[key] === value }, o.label ?? o));
  });
  // Keep an unknown existing value visible rather than silently rewriting it.
  if (obj[key] && !list.some((o) => (o.value ?? o) === obj[key])) {
    select.append(h('option', { value: obj[key], selected: true }, `${obj[key]} (unknown)`));
  }
  return select;
}

/** Chips for a list of ids, click to remove, plus a picker to add. */
function idListCell(obj, key, options, addLabel) {
  const list = typeof options === 'function' ? options() : options;
  const label = (id) => (list.find((o) => o.value === id) || {}).label || id;
  const wrap = h('div.chips');
  (obj[key] || []).forEach((id) => {
    wrap.append(h('button.chip.on', {
      type: 'button', title: 'Remove',
      onclick: () => { obj[key] = obj[key].filter((x) => x !== id); markDirty(); render(); },
    }, label(id) + ' ×'));
  });
  const picker = h('select', {
    onchange: (e) => {
      if (!e.target.value) return;
      obj[key] = [...(obj[key] || []), e.target.value];
      markDirty(); render();
    },
  }, h('option', { value: '' }, addLabel || '+ add'));
  list.filter((o) => !(obj[key] || []).includes(o.value))
    .forEach((o) => picker.append(h('option', { value: o.value }, o.label)));
  wrap.append(picker);
  return wrap;
}

/** Toggle chips for a set of days. */
function dayChipsCell(obj, key) {
  const wrap = h('div.chips');
  state.config.days.forEach((day) => {
    const on = (obj[key] || []).includes(day);
    wrap.append(h(`button.chip${on ? '.on' : ''}`, {
      type: 'button',
      onclick: () => {
        const set = new Set(obj[key] || []);
        set.has(day) ? set.delete(day) : set.add(day);
        obj[key] = state.config.days.filter((d) => set.has(d));
        markDirty(); render();
      },
    }, day));
  });
  return wrap;
}

/** Opens the per-slot unavailability picker. */
function slotPickerCell(teacher) {
  const count = (teacher.unavailable_slots || []).length;
  return h(`button.chip.slots${count ? ' has' : ''}`, {
    type: 'button',
    onclick: () => openSlotPicker(teacher),
  }, count ? `${count} slot${count > 1 ? 's' : ''} blocked` : 'pick slots');
}

function openSlotPicker(teacher) {
  const periods = opts.teachingPeriods();
  const blocked = new Set((teacher.unavailable_slots || []).map((s) => `${s.day}|${s.period}`));

  const table = h('table.picker');
  table.append(h('thead', h('tr',
    h('th', 'Time'),
    ...state.config.days.map((d) => h('th', d)))));
  const body = h('tbody');
  periods.forEach((p) => {
    const row = h('tr', h('td.lbl', `${p.start}–${p.end}`));
    state.config.days.forEach((day) => {
      const key = `${day}|${p.id}`;
      const cell = h('td');
      const btn = h(`button.${blocked.has(key) ? 'on' : 'off'}`, {
        type: 'button',
        onclick: () => {
          blocked.has(key) ? blocked.delete(key) : blocked.add(key);
          btn.className = blocked.has(key) ? 'on' : 'off';
          btn.textContent = blocked.has(key) ? 'blocked' : '';
        },
      }, blocked.has(key) ? 'blocked' : '');
      cell.append(btn);
      row.append(cell);
    });
    body.append(row);
  });
  table.append(body);

  openModal(
    `Unavailable slots — ${teacher.name || teacher.id}`,
    'Click any slot to block it. Blocked slots are a hard constraint: the solver will never ' +
    'place this teacher there. Whole days off are quicker to set with the day chips.',
    table,
    () => {
      teacher.unavailable_slots = [...blocked].map((k) => {
        const [day, period] = k.split('|');
        return { day, period };
      });
      markDirty();
    }
  );
}

function openModal(title, hint, content, onSave) {
  const root = $('#modal-root');
  const close = () => { root.textContent = ''; render(); };
  root.append(h('div.backdrop', { onclick: (e) => { if (e.target.className === 'backdrop') close(); } },
    h('div.modal',
      h('h3', title),
      hint ? h('p', hint) : null,
      content,
      h('div.toolbar',
        h('span.grow'),
        h('button.btn', { onclick: close }, 'Cancel'),
        h('button.btn.primary', { onclick: () => { onSave(); close(); } }, 'Apply')))));
}

/* ------------------------------------------------------------------ *
 * Generic editable table
 * ------------------------------------------------------------------ */
function editTable(rows, columns, makeRow, { onDelete } = {}) {
  const table = h('table.edit');
  table.append(h('thead', h('tr',
    ...columns.map((c) => h('th', c.label)),
    h('th', ''))));

  const body = h('tbody');
  rows.forEach((row, index) => {
    body.append(h('tr',
      ...columns.map((c) => h('td', c.cell(row, index))),
      h('td.actions',
        h('button.icon', {
          title: 'Delete row',
          onclick: () => {
            rows.splice(index, 1);
            if (onDelete) onDelete(row);
            markDirty(); render();
          },
        }, '×'))));
  });
  table.append(body);

  const wrap = h('div');
  wrap.append(h('div.tbl-wrap', table));
  if (!rows.length) wrap.append(h('div.empty', 'Nothing here yet — add a row to get started.'));
  wrap.append(h('div.toolbar', { style: 'margin-top:12px' },
    h('button.btn', {
      onclick: () => { rows.push(makeRow()); markDirty(); render(); },
    }, '+ Add row')));
  return wrap;
}

function panel(title, hint, ...content) {
  return h('div.panel', h('h2', title), hint ? h('p.hint', hint) : null, ...content);
}

/* ------------------------------------------------------------------ *
 * Tabs
 * ------------------------------------------------------------------ */
const TABS = [
  ['grid', 'Time grid', 'The shape of the week',
    'Set the working days and the periods in a day. Breaks do real work here: they split the ' +
    'day into morning and afternoon, and a practical can never run across one.'],
  ['classes', 'Divisions & batches', 'Who is being taught',
    'A division is the group taught theory together. Its batches split off for practicals and ' +
    'can be in different labs at the same moment.'],
  ['subjects', 'Subjects', 'What is being taught',
    'Theory goes to a whole division, a practical to a single batch. The room type decides ' +
    'which rooms a session is allowed to use.'],
  ['rooms', 'Rooms', 'Where it happens',
    'Every session gets a room and no room is ever double-booked. A room qualifies when its ' +
    'type matches the subject and it is big enough for the audience.'],
  ['teachers', 'Teachers', 'Availability and preferences',
    'Days off and blocked slots are hard limits. The morning or after-break preference is soft: ' +
    'honoured as far as the clash rules allow.'],
  ['workload', 'Workload', 'Who teaches what, to whom',
    'One row per teaching commitment. You allocate the teachers; the solver decides only when ' +
    'and in which room each session lands.'],
  ['pinned', 'Pinned events', 'Fixed points in the week',
    'Mentoring, sports, a guest lecture. Whoever and whatever is listed is treated as busy ' +
    'before anything else is scheduled.'],
  ['options', 'Rules', 'Hard rules and soft goals',
    'The rules below are guaranteed. The weights underneath are traded off against each other ' +
    'only once every hard rule already holds.'],
  ['generate', 'Generate', 'Generate the timetables',
    'Every clash rule is a hard constraint, so the result is either completely clash-free or ' +
    'accompanied by an explanation of what cannot fit.'],
];

const TAB_META = Object.fromEntries(TABS.map(([k, , title, hint]) => [k, { title, hint }]));

function uid(prefix, existing) {
  let n = existing.length + 1;
  const taken = new Set(existing.map((e) => e.id));
  while (taken.has(`${prefix}${n}`)) n += 1;
  return `${prefix}${n}`;
}

function tabGrid() {
  const cfg = state.config;
  const daysPanel = panel(
    'Days',
    'The working week. Removing a day removes every slot on it.',
    h('div.chips', ...['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => {
      const on = cfg.days.includes(day);
      return h(`button.chip${on ? '.on' : ''}`, {
        onclick: () => {
          const set = new Set(cfg.days);
          set.has(day) ? set.delete(day) : set.add(day);
          cfg.days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].filter((d) => set.has(d));
          markDirty(); render();
        },
      }, day);
    })));

  const periodsPanel = panel(
    'Periods',
    'The daily shape, repeated on every day. Mark the lunch or tea slot as a break: breaks split ' +
    'the day into morning and afternoon (that is what "after the break" means for preferences), ' +
    'and a practical can never run across one. Leaving "Half" on auto puts everything before the ' +
    'longest break in the morning.',
    editTable(cfg.periods, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Start', cell: (r) => textCell(r, 'start', '09:00') },
      { label: 'End', cell: (r) => textCell(r, 'end', '10:00') },
      { label: 'Kind', cell: (r) => selectCell(r, 'kind', [
        { value: 'teaching', label: 'teaching' }, { value: 'break', label: 'break' }]) },
      { label: 'Label', cell: (r) => textCell(r, 'label', 'optional') },
      { label: 'Half', cell: (r) => selectCell(r, 'session', [
        { value: 'morning', label: 'morning' }, { value: 'afternoon', label: 'afternoon' }],
        { blank: 'auto' }) },
    ], () => ({ id: uid('p', cfg.periods), start: '', end: '', kind: 'teaching', label: '', session: null })));

  return [daysPanel, periodsPanel];
}

function tabClasses() {
  const cfg = state.config;
  return [
    panel('Divisions',
      'A division is the group taught theory together, e.g. SE-B. The home room is where its ' +
      'lectures are held.',
      editTable(cfg.divisions, [
        { label: 'ID', cell: (r) => textCell(r, 'id', 'SE-B') },
        { label: 'Year', cell: (r) => textCell(r, 'year', 'SE') },
        { label: 'Strength', cell: (r) => numberCell(r, 'strength') },
        { label: 'Home room', cell: (r) => selectCell(r, 'home_room', opts.rooms, { blank: '— none —' }) },
      ], () => ({ id: uid('DIV', cfg.divisions), name: '', year: '', strength: 70, home_room: null }))),

    panel('Batches',
      'Sub-groups that do practicals separately. Batches of the same division can be in different ' +
      'labs at the same time; a lecture for the division blocks all of them.',
      editTable(cfg.batches, [
        { label: 'ID', cell: (r) => textCell(r, 'id', 'SE-B1') },
        { label: 'Division', cell: (r) => selectCell(r, 'division', opts.divisions) },
        { label: 'Strength', cell: (r) => numberCell(r, 'strength') },
      ], () => ({ id: uid('B', cfg.batches), division: (cfg.divisions[0] || {}).id || '', name: '', strength: 25 }))),
  ];
}

function tabSubjects() {
  const cfg = state.config;
  return [panel('Subjects',
    'Theory is taught to a whole division; a practical is taught to one batch. The room type ' +
    'decides which rooms a session may use — it must match a room type below.',
    editTable(cfg.subjects, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Name', cell: (r) => textCell(r, 'name') },
      { label: 'Short', cell: (r) => textCell(r, 'short', 'DBMS') },
      { label: 'Kind', cell: (r) => selectCell(r, 'kind', [
        { value: 'theory', label: 'theory' }, { value: 'practical', label: 'practical' }]) },
      { label: 'Room type', cell: (r) => selectCell(r, 'room_type', opts.roomTypes) },
    ], () => ({ id: uid('SUB', cfg.subjects), name: '', short: '', kind: 'theory', room_type: 'classroom' })))];
}

function tabRooms() {
  const cfg = state.config;
  return [panel('Rooms',
    'Every session gets a room, and no room hosts two sessions at once. A room is eligible when ' +
    'its type matches the subject and its capacity covers the audience.',
    editTable(cfg.rooms, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Name', cell: (r) => textCell(r, 'name') },
      { label: 'Type', cell: (r) => textCell(r, 'type', 'classroom') },
      { label: 'Capacity', cell: (r) => numberCell(r, 'capacity') },
    ], () => ({ id: uid('R', cfg.rooms), name: '', type: 'classroom', capacity: 70 })))];
}

function tabTeachers() {
  const cfg = state.config;
  return [panel('Teachers and their preferences',
    'Days off and blocked slots are hard: nothing is ever scheduled there. The morning / ' +
    'after-break preference is soft — the solver honours as much of it as it can once every ' +
    'clash rule is satisfied, and weight decides who wins when two preferences compete. ' +
    'Max/day is an optional hard cap; leave it empty for no limit.',
    editTable(cfg.teachers, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Name', cell: (r) => textCell(r, 'name') },
      { label: 'Prefers', cell: (r) => selectCell(r, 'session_preference', [
        { value: 'none', label: 'no preference' },
        { value: 'morning', label: 'morning' },
        { value: 'afternoon', label: 'after the break' }]) },
      { label: 'Weight', cell: (r) => numberCell(r, 'preference_weight', { min: 1, max: 5 }) },
      { label: 'Max/day', cell: (r) => numberCell(r, 'max_per_day', { nullable: true }) },
      { label: 'Days off', cell: (r) => dayChipsCell(r, 'unavailable_days') },
      { label: 'Blocked slots', cell: (r) => slotPickerCell(r) },
    ], () => ({
      id: uid('T', cfg.teachers), name: '', short: '', unavailable_days: [],
      unavailable_slots: [], session_preference: 'none', preference_weight: 3, max_per_day: null,
    })))];
}

function tabWorkload() {
  const cfg = state.config;
  const targetCell = (row) => {
    const wrap = h('div.chips');
    wrap.append(selectCell(row.target, 'kind', [
      { value: 'division', label: 'division' }, { value: 'batch', label: 'batch' }]));
    wrap.append(selectCell(row.target, 'id',
      row.target.kind === 'batch' ? opts.batches : opts.divisions));
    return wrap;
  };

  return [panel('Workload — who teaches what, to whom',
    'One row per teaching commitment. You allocate the teacher; the solver decides only when and ' +
    'where it happens. "Sessions/week" is how many separate meetings, "slots each" how long one ' +
    'meeting is — a practical is normally 1 session of 2 slots, and those 2 slots are always ' +
    'back-to-back on one day. Leave rooms empty to let any suitable room be used.',
    editTable(cfg.assignments, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Subject', cell: (r) => selectCell(r, 'subject', opts.subjects) },
      { label: 'Taught to', cell: targetCell },
      { label: 'Teacher(s)', cell: (r) => idListCell(r, 'teachers', opts.teachers, '+ teacher') },
      { label: 'Sessions/wk', cell: (r) => numberCell(r, 'sessions_per_week', { min: 1 }) },
      { label: 'Slots each', cell: (r) => numberCell(r, 'slots_per_session', { min: 1, max: 6 }) },
      { label: 'Rooms', cell: (r) => idListCell(r, 'allowed_rooms', opts.rooms, 'any suitable') },
    ], () => ({
      id: uid('A', cfg.assignments),
      subject: (cfg.subjects[0] || {}).id || '',
      target: { kind: 'division', id: (cfg.divisions[0] || {}).id || '' },
      teachers: [], sessions_per_week: 1, slots_per_session: 1, allowed_rooms: [],
    })))];
}

function tabPinned() {
  const cfg = state.config;
  return [panel('Pinned events',
    'Fixed blocks the solver must schedule around — mentoring, sports, a guest lecture. Whoever ' +
    'and whatever is listed is treated as busy at that time before anything else is placed.',
    editTable(cfg.pinned, [
      { label: 'ID', cell: (r) => textCell(r, 'id') },
      { label: 'Name', cell: (r) => textCell(r, 'name') },
      { label: 'Day', cell: (r) => selectCell(r, 'day', opts.days) },
      { label: 'Period', cell: (r) => selectCell(r, 'period',
        opts.teachingPeriods().map((p) => ({ value: p.id, label: `${p.id} (${p.start})` }))) },
      { label: 'Slots', cell: (r) => numberCell(r, 'slots_per_session', { min: 1, max: 6 }) },
      { label: 'Divisions', cell: (r) => {
        // Pinned targets are stored as {kind,id}; expose the common case (whole
        // divisions) as a simple id list.
        const shim = { ids: (r.targets || []).map((t) => t.id) };
        const cell = idListCell(shim, 'ids', opts.divisions, '+ division');
        const sync = () => { r.targets = shim.ids.map((id) => ({ kind: 'division', id })); };
        cell.querySelectorAll('button').forEach((b) => b.addEventListener('click', sync));
        cell.querySelectorAll('select').forEach((s) => s.addEventListener('change', sync));
        return cell;
      } },
      { label: 'Teachers', cell: (r) => idListCell(r, 'teachers', opts.teachers, '+ teacher') },
      { label: 'Room', cell: (r) => selectCell(r, 'room', opts.rooms, { blank: '— none —' }) },
    ], () => ({
      id: uid('PIN', cfg.pinned), name: 'New event',
      day: cfg.days[0] || 'Mon', period: (opts.teachingPeriods()[0] || {}).id || '',
      slots_per_session: 1, targets: [], teachers: [], room: null,
    })))];
}

function tabOptions() {
  const cfg = state.config;
  const check = (obj, key, title, hint) => h('label.check',
    h('input', {
      type: 'checkbox', checked: obj[key],
      onchange: (e) => { obj[key] = e.target.checked; markDirty(); },
    }),
    h('span', h('strong', title), h('small', hint)));

  return [
    panel('Scheduling rules', 'Hard rules. These are guaranteed, not preferred.',
      check(cfg.options, 'one_session_per_day', 'One session of a subject per day',
        'Stops the same subject appearing twice in a division\'s day. Relaxed automatically for ' +
        'a subject that needs more sessions than there are days.'),
      check(cfg.options, 'parallel_batch_labs', 'Practicals run for all batches together',
        'Every batch of a division is in a practical at the same time, or none is — so no batch ' +
        'sits idle while its siblings are in a lab. Needs one room per batch at that moment.'),
      h('div.row',
        h('label.field', 'Solver time limit (seconds)',
          h('input', {
            type: 'number', min: 1, max: 600, value: cfg.options.max_seconds,
            onchange: (e) => { cfg.options.max_seconds = Number(e.target.value); markDirty(); },
          })),
        h('label.field', 'Random seed',
          h('input', {
            type: 'number', value: cfg.options.random_seed,
            onchange: (e) => { cfg.options.random_seed = Number(e.target.value); markDirty(); },
          })))),

    panel('Preference weights',
      'Soft goals, traded off against each other after every hard rule is satisfied. Set a weight ' +
      'to 0 to drop that goal entirely, which also makes the solve faster.',
      h('div.row',
        h('label.field', 'Teacher morning / after-break preference',
          h('input', {
            type: 'number', min: 0, value: cfg.weights.session_preference,
            onchange: (e) => { cfg.weights.session_preference = Number(e.target.value); markDirty(); },
          })),
        h('label.field', 'Even weekly spread for students',
          h('input', {
            type: 'number', min: 0, value: cfg.weights.even_spread,
            onchange: (e) => { cfg.weights.even_spread = Number(e.target.value); markDirty(); },
          })),
        h('label.field', 'Avoid gaps in a student\'s day',
          h('input', {
            type: 'number', min: 0, value: cfg.weights.student_gap,
            onchange: (e) => { cfg.weights.student_gap = Number(e.target.value); markDirty(); },
          })))),
  ];
}

/* ------------------------------------------------------------------ *
 * Generate tab
 * ------------------------------------------------------------------ */
function issueList(issues) {
  if (!issues.length) return null;
  return h('div', ...issues.map((i) => h(`div.msg.${i.level}`,
    h('strong', i.level === 'error' ? 'Cannot schedule: ' : 'Note: '),
    i.message)));
}

/** One column of the Carbs / Protein / Fat style metric row. */
function metric(key, value, { fraction = null, colour = '', serif = false } = {}) {
  return h('div.metric',
    h('span.k', key),
    fraction === null ? null : h('div.track',
      h('span', { class: `fill ${colour}`, style: `width:${Math.round(100 * fraction)}%` })),
    h('div', { class: serif ? 'v serif' : 'v' }, value));
}

function renderSummary(s) {
  const prefRatio = s.preference_total ? s.preference_matched / s.preference_total : null;
  const cards = h('div.metrics',
    metric('Status', s.status, { serif: true }),
    metric('Sessions placed', `${s.sessions_placed} / ${s.sessions_requested}`, {
      fraction: s.sessions_requested ? s.sessions_placed / s.sessions_requested : 0,
      colour: s.sessions_placed === s.sessions_requested ? '' : 'orange',
    }),
    metric('Slots filled', s.slots_filled, { serif: true }),
    prefRatio === null ? null : metric('Preferences met',
      `${s.preference_matched} / ${s.preference_total}`,
      { fraction: prefRatio, colour: 'pink' }),
    metric('Solve time', `${s.solve_seconds}s`, { serif: true }));

  const verdict = s.clashes.length
    ? h('div.msg.error', h('strong', `${s.clashes.length} clash(es) found. `),
      'This should be impossible — please report it.',
      h('ul.plain', ...s.clashes.slice(0, 8).map((c) => h('li', c))))
    : (s.sessions_placed
      ? h('div.msg.ok', h('strong', 'Verified clash-free. '),
        'Re-checked independently of the solver: no teacher, no batch and no room is ' +
        'double-booked anywhere, and every practical sits on back-to-back slots.')
      : null);

  const notes = s.messages.map((m) => h('div.msg.info', m));
  const unplaced = s.unplaced.length
    ? h('div.msg.warning',
      h('strong', 'Could not place everything: '),
      h('ul.plain', ...s.unplaced.map((u) => h('li',
        `${u.assignment} — ${u.placed} of ${u.requested} sessions placed. ${u.reason}`))))
    : null;

  return h('div', cards, verdict, ...notes, unplaced);
}

/** Everything selectable for a given view kind. */
function viewOptions(kind) {
  const cfg = state.config;
  return ({
    division: () => cfg.divisions.map((d) => ({ value: d.id, label: d.id })),
    batch: () => cfg.batches.map((b) => ({ value: b.id, label: b.id })),
    teacher: () => cfg.teachers.map((t) => ({ value: t.id, label: t.name || t.id })),
    room: () => cfg.rooms.map((r) => ({ value: r.id, label: `${r.id} — ${r.name || r.type}` })),
  }[kind] || (() => []))();
}

/** Settle on something to show, so the first render after a solve isn't blank. */
function resolveViewId() {
  const options = viewOptions(state.viewKind);
  if (!state.viewId || !options.some((o) => o.value === state.viewId)) {
    state.viewId = (options[0] || {}).value || null;
  }
  return state.viewId;
}

function viewSelector() {
  const groups = { [state.viewKind]: viewOptions(state.viewKind) };
  resolveViewId();

  const kind = h('select', {
    onchange: (e) => { state.viewKind = e.target.value; state.viewId = null; loadView(); },
  }, ...[['division', 'Class timetable'], ['batch', 'Batch timetable'],
         ['teacher', 'Teacher timetable'], ['room', 'Room timetable']]
    .map(([v, l]) => h('option', { value: v, selected: state.viewKind === v }, l)));

  const who = h('select', {
    onchange: (e) => { state.viewId = e.target.value; loadView(); },
  }, ...groups[state.viewKind].map((o) =>
    h('option', { value: o.value, selected: state.viewId === o.value }, o.label)));

  return h('div.toolbar', kind, who, h('span.grow'),
    h('a', { href: '/api/export/all.html', target: '_blank' },
      h('button.btn', 'Open all timetables (printable)')),
    h('a', { href: '/api/export/all.csv', target: '_blank' },
      h('button.btn', 'Download CSV')));
}

function entryNode(entry) {
  const who = entry.teachers.map((t) => t.name).join(', ') || '—';
  return h(`div.entry${entry.kind === 'practical' ? '.practical' : ''}`,
    entry.target_kind === 'batch' ? h('span.tag', entry.target) : null,
    h('span.subj', entry.subject_short),
    entry.span > 1 ? h('span.dbl', 'double period') : null,
    h('span.who', who),
    entry.room_name ? h('span.where', entry.room_name) : null);
}

function gridNode(view) {
  const table = h('table.tt');
  table.append(h('thead', h('tr', h('th', 'Time'), ...view.days.map((d) => h('th', d)))));
  const body = h('tbody');

  view.periods.forEach((p) => {
    if (p.kind === 'break') {
      body.append(h('tr.break',
        h('td.time', `${p.start}–${p.end}`),
        h('td', { colspan: view.days.length }, p.label || 'Break')));
      return;
    }
    const row = h('tr', h('td.time', `${p.start}–${p.end}`));
    view.days.forEach((day) => {
      const entries = (view.cells[day] || {})[p.id] || [];
      const shown = entries.filter((e) => !e.continuation);
      // A continuation slot of a double period stays tinted but carries no
      // entry of its own -- the session is printed on the slot it starts in.
      const carryOver = !shown.length && entries.length;
      const cell = h(`td${carryOver ? '.cont' : (entries.length ? '.filled' : '')}`);
      shown.forEach((e) => cell.append(entryNode(e)));
      if (carryOver) cell.append(h('span.cont-mark', '↑ continues'));
      row.append(cell);
    });
    body.append(row);
  });
  table.append(body);
  return h('div.tt-wrap', table);
}

function tabGenerate() {
  const out = [];

  // Actions and headline numbers share one card, so the ring in the hero and
  // the figures behind it read as a single unit.
  out.push(panel(
    state.summary ? 'Result' : 'Generate',
    state.summary ? null
      : 'Checking first is optional — generating always runs the checks. If anything is ' +
        'impossible you get an explanation instead of a silent failure.',
    h('div.toolbar',
      h('button.btn.primary', { disabled: state.busy, onclick: doSolve },
        state.busy ? 'Solving…' : (state.summary ? 'Generate again' : 'Generate timetables')),
      h('button.btn', { disabled: state.busy, onclick: doValidate }, 'Check configuration')),
    state.summary ? renderSummary(state.summary) : null,
    issueList(state.issues)));

  if (state.summary) {
    const body = h('div');
    body.append(viewSelector());
    if (state.view) {
      body.append(h('div.grid-head',
        h('h3', state.view.title),
        state.view.load !== undefined ? h('span.load', `${state.view.load} slots/week`) : null,
        state.view.honoured && state.view.honoured.total
          ? h('span.load', `· ${state.view.honoured.matched}/${state.view.honoured.total} slots ` +
            `in preferred half of day`)
          : null));
      body.append(gridNode(state.view));
    }
    out.push(panel('Timetables', null, body));

    const teachers = state.summary.teachers.filter((t) => t.load);
    const max = Math.max(1, ...teachers.map((t) => t.load));
    out.push(panel('Teacher load and preference match', null,
      h('table.load-table',
        h('thead', h('tr', h('th', 'Teacher'), h('th', 'Prefers'),
          h('th', { class: 'num' }, 'Slots/wk'), h('th', 'Load'),
          h('th', { class: 'num' }, 'Preferred half'))),
        h('tbody', ...teachers.map((t) => h('tr',
          h('td', t.name),
          h('td', t.preference === 'none' ? '—'
            : (t.preference === 'morning' ? 'morning' : 'after break')),
          h('td.num', t.load),
          h('td', h('span.bar', { style: `width:${Math.round(100 * t.load / max)}%` })),
          h('td.num', t.of ? `${t.matched}/${t.of}` : '—')))))));
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * Actions
 * ------------------------------------------------------------------ */
async function saveConfig() {
  const result = await api('/api/config', {
    method: 'PUT', body: JSON.stringify(state.config),
  });
  state.dirty = false;
  $('#save-state').textContent = 'saved';
  setTimeout(() => { if (!state.dirty) $('#save-state').textContent = ''; }, 2500);
  return result;
}

async function doValidate() {
  state.busy = true; render();
  try {
    if (state.dirty) await saveConfig();
    const result = await api('/api/validate', { method: 'POST' });
    state.issues = result.issues;
    if (!result.issues.length) {
      state.issues = [{ level: 'ok', message: 'No problems found. Everything asked for fits.' }];
    }
  } catch (err) {
    state.issues = [{ level: 'error', message: String(err.message || err) }];
  } finally {
    state.busy = false; render();
  }
}

async function doSolve() {
  state.busy = true; state.summary = null; state.view = null; render();
  try {
    if (state.dirty) await saveConfig();
    const result = await api('/api/solve', { method: 'POST' });
    state.issues = result.issues;
    state.summary = result.summary;
    if (result.summary) await loadView();
  } catch (err) {
    state.issues = [{ level: 'error', message: String(err.message || err) }];
  } finally {
    state.busy = false; render();
  }
}

async function loadView() {
  if (!resolveViewId()) { state.view = null; render(); return; }
  try {
    state.view = await api(`/api/timetable/${state.viewKind}/${encodeURIComponent(state.viewId)}`);
  } catch (_) {
    state.view = null;
  }
  render();
}

async function doReset() {
  state.config = await api('/api/config/reset', { method: 'POST' });
  state.dirty = false;
  state.summary = null; state.view = null; state.issues = [];
  $('#save-state').textContent = '';
  render();
}

/* ------------------------------------------------------------------ *
 * Hero
 * ------------------------------------------------------------------ */

/** Progress ring. The CSS mask hollows the centre, so the label is a sibling
 *  layered over it rather than a child that the mask would eat. */
function ringNode(fraction, big, small) {
  return h('div.ring-wrap',
    h('div.ring', { style: `--p:${Math.round(100 * fraction)}%` }),
    h('div.ring-label', h('div.ring-v', big), h('span.ring-k', small)));
}

function renderHeroBody() {
  const body = $('#hero-body');
  body.textContent = '';

  const s = state.summary;
  if (state.tab === 'generate' && s) {
    const placed = s.sessions_requested ? s.sessions_placed / s.sessions_requested : 0;
    const prefs = s.preference_total
      ? Math.round(100 * s.preference_matched / s.preference_total) + '%'
      : '—';
    body.append(h('div.hero-stats',
      h('div.side', h('div.side-v', s.slots_filled), h('span.side-k', 'slots filled')),
      ringNode(placed, s.sessions_placed,
        `of ${s.sessions_requested} sessions`),
      h('div.side', h('div.side-v', prefs), h('span.side-k', 'preferences met'))));
    return;
  }

  const meta = TAB_META[state.tab] || {};
  body.append(h('div.hero-tagline', h('h1', meta.title || ''), h('p', meta.hint || '')));
}

/* ------------------------------------------------------------------ *
 * Render
 * ------------------------------------------------------------------ */
const RENDERERS = {
  grid: tabGrid, classes: tabClasses, subjects: tabSubjects, rooms: tabRooms,
  teachers: tabTeachers, workload: tabWorkload, pinned: tabPinned,
  options: tabOptions, generate: tabGenerate,
};

function render() {
  const nav = $('#nav');
  nav.textContent = '';
  TABS.forEach(([key, label]) => {
    nav.append(h(`button${state.tab === key ? '.active' : ''}`, {
      onclick: () => { state.tab = key; render(); },
    }, label));
  });

  renderHeroBody();

  const main = $('#main');
  main.textContent = '';
  if (!state.config) { main.append(h('div.empty', 'Loading…')); return; }
  (RENDERERS[state.tab] || tabGrid)().forEach((node) => node && main.append(node));
}

async function boot() {
  $('#btn-save').addEventListener('click', () => saveConfig().then(render));
  $('#btn-reset').addEventListener('click', () => {
    openModal('Reset configuration?',
      'This discards every edit and restores the shipped sample dataset.',
      h('div'), doReset);
  });
  state.config = await api('/api/config');
  render();
}

boot();
