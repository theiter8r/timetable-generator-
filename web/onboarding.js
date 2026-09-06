'use strict';

/* ------------------------------------------------------------------ *
 * Guided setup.
 *
 * The first time the app opens -- and any time "Setup guide" is pressed --
 * this takes over the page and walks the head of department through the same
 * editors the full app uses, one step at a time, in an order where each answer
 * is possible to give: rooms before subjects (a subject points at a room type),
 * teachers before workload (workload allocates teachers).
 *
 * Nothing is ever left unsaved. Every edit schedules a save of the whole config
 * plus the step you are on, so closing the laptop mid-way and coming back
 * tomorrow resumes exactly where you were. Saving deliberately does not
 * re-render the editors -- only the header, the rail and the checklist -- so a
 * save can never steal the caret out of the field you are typing in.
 *
 * Loaded before app.js; every function here runs only after app.js has defined
 * `state`, `h`, `api` and the tab renderers it reuses.
 * ------------------------------------------------------------------ */

const wizard = {
  active: false,
  state: null,        // OnboardingState from the server
  steps: [],          // per-step report, refreshed by every save
  saving: false,
  queued: false,      // an edit landed while a save was in flight
  timer: null,        // debounce handle
  savedAt: '',
  errors: [],         // why the last save did not stick
  pristine: false,    // nothing entered yet, so nothing to lose
};

const SAVE_DEBOUNCE_MS = 700;

/** Extra guidance per step: what this step wants, in the HOD's terms. */
const WIZARD_HELP = {
  welcome: {
    needs: [],
  },
  grid: {
    needs: [
      'The days you teach on.',
      'Every period of a normal day, in order, with start and end times.',
      'Lunch and tea marked as a break rather than a teaching period.',
    ],
    note: 'This is the shape of one week. Every timetable the app produces is a ' +
      'grid of exactly these days and periods, so it is worth getting right first.',
  },
  classes: {
    needs: [
      'One division per group that attends lectures together (SE-B, TE-A …).',
      'The batches each division splits into for practicals (SE-B1, SE-B2 …).',
      'Student strength, so rooms can be checked for capacity.',
    ],
    note: 'A lecture blocks the whole division; its batches can be in different ' +
      'labs at the same moment. A year with no practicals needs no batches.',
  },
  rooms: {
    needs: [
      'Every classroom and lab the department can use.',
      'A type for each — classroom, computer_lab, electronics_lab, anything you like.',
      'Capacity, so a division is never put in a room too small for it.',
    ],
    note: 'Types are free text and matched against subjects in the next step, so ' +
      'name them the way your department already does.',
  },
  subjects: {
    needs: [
      'Every subject taught this term.',
      'Theory or practical for each one.',
      'The room type it needs — it must match a type you entered on the last step.',
    ],
  },
  teachers: {
    needs: [
      'Every member of teaching staff, with their name as it should appear on a timetable.',
      'Days off and blocked slots — these are hard, nothing is ever scheduled there.',
      'Optionally a morning or after-break preference, and how strongly it counts.',
    ],
    note: 'Preferences are honoured as far as the clash rules allow; days off are never ' +
      'touched. Leave Max/day empty unless you want a hard cap.',
  },
  workload: {
    needs: [
      'One row for every teaching commitment: subject, who it is taught to, who teaches it.',
      'How many separate sessions a week, and how many slots one session runs for.',
      'Optionally a restricted room list; leave it empty for "any suitable room".',
    ],
    note: 'This is the heart of the configuration. You allocate the teachers here — the ' +
      'solver decides only when and in which room each session lands. A two-hour ' +
      'practical is one session of two slots, and those two slots are always ' +
      'back-to-back on the same day.',
  },
  pinned: {
    needs: ['Anything already fixed in the week: mentoring, sports, a guest lecture.'],
    note: 'Optional. Whoever and whatever you list is treated as busy at that time before ' +
      'anything else is scheduled.',
  },
  options: {
    needs: [],
    note: 'Optional — the defaults are what most departments want. Come back here if the ' +
      'first timetable is not shaped the way you expected.',
  },
  review: { needs: [] },
};

/* ------------------------------------------------------------------ *
 * State plumbing
 * ------------------------------------------------------------------ */
function wizardActive() {
  return !!(wizard.active && wizard.state);
}

function wizardInit(payload) {
  wizard.state = payload.state;
  wizard.steps = payload.steps || [];
  wizard.savedAt = payload.saved_at || '';
  wizard.pristine = !!payload.pristine;
  wizard.errors = [];
  wizard.active = !payload.state.finished;
  if (!wizard.steps.some((s) => s.id === wizard.state.step)) {
    wizard.state.step = (wizard.steps[0] || {}).id || 'welcome';
  }
}

function stepIndex(id) {
  return wizard.steps.findIndex((s) => s.id === (id || wizard.state.step));
}

function currentStep() {
  return wizard.steps[Math.max(0, stepIndex())] || wizard.steps[0];
}

/** Called by markDirty() for every edit made while the wizard is up. */
function wizardTouched() {
  state.dirty = true;
  clearTimeout(wizard.timer);
  wizard.timer = setTimeout(() => wizardSave(), SAVE_DEBOUNCE_MS);
  paintSaveState('saving');
}

async function wizardSave() {
  if (wizard.saving) { wizard.queued = true; return; }
  clearTimeout(wizard.timer);
  wizard.timer = null;
  wizard.saving = true;
  paintSaveState('saving');

  try {
    const result = await api('/api/onboarding', {
      method: 'PUT',
      body: JSON.stringify({ config: state.config, state: wizard.state }),
    });
    wizard.steps = result.steps || wizard.steps;
    if (result.ok) {
      wizard.errors = [];
      wizard.savedAt = result.saved_at || '';
      wizard.pristine = !!result.pristine;
      state.dirty = false;
      // A saved config invalidates the last timetable; the server drops it too.
      state.summary = null;
      state.view = null;
    } else {
      wizard.errors = result.errors || ['The configuration could not be saved.'];
    }
  } catch (err) {
    wizard.errors = [`Could not reach the app: ${err.message || err}. ` +
      'Your last change is still on screen — it will save when the connection is back.'];
  } finally {
    wizard.saving = false;
  }

  if (wizard.queued) { wizard.queued = false; return wizardSave(); }
  refreshWizardChrome();
}

/** Move to another step, saving what is on screen on the way out.
 *
 * The move happens first and the save catches up underneath: the config is
 * already in memory, so there is nothing to wait for on screen, and a save that
 * takes a moment must never make Next feel broken. Whatever the save comes back
 * with lands in the checklist a moment later. */
async function wizardGo(stepId) {
  const from = wizard.state.step;
  if (from !== stepId && !wizard.state.visited.includes(from)) {
    wizard.state.visited.push(from);
  }
  wizard.state.step = stepId;
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  await wizardSave();
}

async function wizardStartFrom(source) {
  const result = await api('/api/onboarding/start', {
    method: 'POST',
    body: JSON.stringify({
      source,
      institution: (state.config.institution || '').trim(),
      department: (state.config.department || '').trim(),
    }),
  });
  state.config = result.config;
  state.summary = null;
  state.view = null;
  state.issues = [];
  wizardInit(result);
  render();
}

async function finishWizard({ generate } = {}) {
  await wizardSave();
  if (wizard.errors.length) { refreshWizardChrome(); return; }
  const result = await api('/api/onboarding/finish', { method: 'POST' });
  wizard.state = result.state;
  wizard.active = false;
  state.tab = generate ? 'generate' : 'grid';
  render();
  if (generate) doSolve();
}

async function reopenWizard() {
  const result = await api('/api/onboarding/reopen', { method: 'POST' });
  state.config = await api('/api/config');
  wizardInit(result);
  render();
}

// A save is pending far more often than the window closes, but when it does
// close mid-debounce we still want the edit to land.
window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden' && wizardActive() && wizard.timer) wizardSave();
});

/* ------------------------------------------------------------------ *
 * Chrome that a save refreshes in place (never the editors themselves)
 * ------------------------------------------------------------------ */
function paintSaveState(mode) {
  const node = $('#save-state');
  if (!node) return;
  if (mode === 'saving') { node.textContent = 'saving…'; node.className = 'save-state'; return; }
  if (wizard.errors.length) {
    node.textContent = 'not saved';
    node.className = 'save-state bad';
    return;
  }
  const when = wizard.savedAt ? new Date(wizard.savedAt) : null;
  node.textContent = when && !isNaN(when)
    ? `saved ${when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : 'saved';
  node.className = 'save-state ok';
}

function refreshWizardChrome() {
  if (!wizardActive()) return;
  paintSaveState();
  const rail = $('#nav');
  if (rail) { rail.textContent = ''; rail.append(...wizardRail()); }
  const check = $('#wizard-check');
  if (check) { check.textContent = ''; check.append(...checklistNodes(currentStep())); }
  const foot = $('#wizard-foot');
  if (foot) { foot.textContent = ''; foot.append(...footNodes()); }
}

/* ------------------------------------------------------------------ *
 * Pieces
 * ------------------------------------------------------------------ */
function wizardRail() {
  return wizard.steps.map((step, i) => {
    const here = step.id === wizard.state.step;
    const mark = step.errors.length ? '!' : (step.done ? '✓' : String(i));
    const cls = step.errors.length ? 'bad' : (step.done ? 'ok' : 'todo');
    return h(`button.step-tab.${cls}${here ? '.active' : ''}`, {
      onclick: () => wizardGo(step.id),
      title: step.lede,
    }, h('span.step-dot', mark), h('span.step-name', step.title));
  });
}

function checklistNodes(step) {
  const help = WIZARD_HELP[step.id] || {};
  const out = [];

  if (wizard.errors.length) {
    out.push(h('div.msg.error',
      h('strong', 'Not saved. '),
      'Fix this and it will save itself:',
      h('ul.plain', ...wizard.errors.map((e) => h('li', e)))));
  }

  if (help.needs && help.needs.length) {
    out.push(h('div.needs',
      h('span.eyebrow', 'What this step needs'),
      h('ul.plain', ...help.needs.map((n) => h('li', n)))));
  }

  if (step.errors.length) {
    out.push(h('div.msg.error',
      h('strong', 'Still to do: '),
      h('ul.plain', ...step.errors.map((e) => h('li', e)))));
  } else if (!step.optional && step.done) {
    out.push(h('div.msg.ok', h('strong', 'This step is complete. '),
      step.summary || 'Everything needed here is filled in.'));
  }

  if (step.warnings.length) {
    out.push(h('div.msg.warning',
      h('strong', 'Worth a look: '),
      h('ul.plain', ...step.warnings.map((w) => h('li', w)))));
  }
  return out;
}

function footNodes() {
  const index = stepIndex();
  const step = currentStep();
  const last = index === wizard.steps.length - 1;
  const blocked = step.errors.length > 0;

  const nodes = [];
  if (index > 0) {
    nodes.push(h('button.btn', {
      onclick: () => wizardGo(wizard.steps[index - 1].id),
    }, '← Back'));
  }
  nodes.push(h('span.grow'));

  if (blocked) {
    nodes.push(h('button.link', {
      onclick: () => wizardGo(wizard.steps[Math.min(index + 1, wizard.steps.length - 1)].id),
      title: 'Move on and come back to this step later',
    }, 'Leave it for now'));
  }

  if (step.id === 'welcome' && !wizard.state.started_from) {
    // Nothing has been chosen yet, so the two cards are the way on, not a Next.
  } else if (last) {
    nodes.push(h('button.btn', { onclick: () => finishWizard({}) }, 'Finish, edit later'));
    nodes.push(h('button.btn.primary', {
      disabled: blocked, onclick: () => finishWizard({ generate: true }),
    }, 'Finish & generate timetables'));
  } else {
    nodes.push(h('button.btn.primary', {
      disabled: blocked,
      onclick: () => wizardGo(wizard.steps[index + 1].id),
    }, `Next: ${wizard.steps[index + 1].title} →`));
  }
  return nodes;
}

/* ------------------------------------------------------------------ *
 * The two steps that are not just a reused editor
 * ------------------------------------------------------------------ */
function stepWelcome() {
  const cfg = state.config;
  const started = !!wizard.state.started_from;
  // What is on screen right now only counts as *theirs* once something has been
  // saved: on a first run the app is showing the shipped sample, which nobody
  // typed and nobody minds losing.
  const filled = !wizard.pristine
    && (cfg.divisions.length || cfg.assignments.length || cfg.teachers.length);

  const nameField = (key, label, placeholder) => h('label.field', label,
    h('input', {
      value: cfg[key] || '', placeholder,
      oninput: (e) => { cfg[key] = e.target.value; markDirty(); },
    }));

  const choice = (title, body, action) => h('button.choice', { onclick: action },
    h('strong', title), h('span', body));

  const confirmThenStart = (source, title, warning) => {
    if (!filled) return wizardStartFrom(source);
    return openModal(title, warning, h('div'), () => wizardStartFrom(source));
  };

  return [
    panel('Who is this timetable for?',
      'Both are optional and both head the printable timetables, so a sheet handed to a ' +
      'student says where it came from. You can change them at any time.',
      h('div.row',
        nameField('institution', 'Institution', 'e.g. Vishwakarma Institute of Technology'),
        nameField('department', 'Department', 'e.g. Computer Engineering'))),

    panel('Where would you like to start?',
      'Setup is ten short steps and saves itself as you go, so you can stop and come back. ' +
      'Nothing is generated until you ask for it.',
      h('div.choices',
        choice('Start from scratch',
          'An empty department with a Monday–Saturday week and a standard six-period day ' +
          'already laid out. You fill in your own divisions, rooms, subjects and staff.',
          () => confirmThenStart('blank', 'Start from scratch?',
            'This clears the configuration currently loaded and begins with an empty ' +
            'department. Anything already entered will be lost.')),
        choice('Load the sample department',
          'A realistic department — 6 divisions, 18 batches, 22 teachers, 84 commitments — ' +
          'to look through or to edit into your own. The quickest way to see what a ' +
          'filled-in configuration looks like.',
          () => confirmThenStart('sample', 'Load the sample department?',
            'This replaces the configuration currently loaded with the shipped sample ' +
            'dataset. Anything already entered will be lost.'))),
      started
        ? h('div.msg.info',
          h('strong', 'Or carry on with what is already here. '),
          `${cfg.divisions.length} divisions, ${cfg.teachers.length} teachers and ` +
          `${cfg.assignments.length} commitments are entered so far — ` +
          'use Next below, or any step in the rail above.')
        : null),
  ];
}

function stepReview() {
  const cfg = state.config;
  const rows = wizard.steps
    .filter((s) => s.summary)
    .map((s) => h('tr',
      h('td', s.title),
      h('td', s.summary),
      h('td', { class: s.errors.length ? 'bad' : 'ok' },
        s.errors.length ? `${s.errors.length} to fix` : 'ready')));

  const blocking = wizard.steps.filter((s) => s.errors.length);
  const heading = [cfg.institution, cfg.department].filter(Boolean).join(' — ');

  return [
    panel('Everything you have entered',
      heading ? `Timetables will be headed “${heading}”.` : null,
      h('div.tbl-wrap', h('table.edit',
        h('thead', h('tr', h('th', 'Step'), h('th', 'Contents'), h('th', 'State'))),
        h('tbody', ...rows)))),

    blocking.length
      ? panel('Before you can generate',
        'Each of these is a step with something still missing. Click a step name in the rail ' +
        'above to go back to it.',
        ...blocking.map((s) => h('div.msg.error',
          h('strong', `${s.title}: `),
          h('ul.plain', ...s.errors.map((e) => h('li', e))))))
      : panel('Ready',
        'Every required step is filled in and the pre-flight checks pass. Generating runs the ' +
        'solver over the whole week; on a department this size it takes a few seconds, and ' +
        'the result is either completely clash-free or comes with an explanation of what ' +
        'could not fit.',
        h('div.msg.ok',
          h('strong', 'Nothing is locked in. '),
          'Every table you have just filled in stays editable from the tabs at the top ' +
          'once setup is finished, and you can walk back through this guide at any time ' +
          'with the "Setup guide" button.')),
  ];
}

/* ------------------------------------------------------------------ *
 * Render
 * ------------------------------------------------------------------ */
const WIZARD_BODY = {
  welcome: stepWelcome,
  grid: () => tabGrid(),
  classes: () => tabClasses(),
  rooms: () => tabRooms(),
  subjects: () => tabSubjects(),
  teachers: () => tabTeachers(),
  workload: () => tabWorkload(),
  pinned: () => tabPinned(),
  options: () => tabOptions(),
  review: stepReview,
};

function renderWizard() {
  document.body.classList.add('wizard');
  const step = currentStep();
  if (!step) { wizard.active = false; render(); return; }
  const index = stepIndex();
  const help = WIZARD_HELP[step.id] || {};
  const requiredSteps = wizard.steps.filter((s) => !s.optional);
  const doneCount = requiredSteps.filter((s) => s.done).length;

  const hero = $('#hero-body');
  hero.textContent = '';
  hero.append(h('div.hero-tagline',
    h('span.eyebrow', `Setup · step ${index + 1} of ${wizard.steps.length}`),
    h('h1', step.title),
    h('p', step.lede),
    h('div.setup-progress',
      h('div.track', h('span.fill', {
        style: `width:${Math.round(100 * doneCount / (requiredSteps.length || 1))}%`,
      })),
      h('span.setup-progress-k',
        `${doneCount} of ${requiredSteps.length} required steps complete`))));

  const rail = $('#nav');
  rail.textContent = '';
  rail.append(...wizardRail());

  const main = $('#main');
  main.textContent = '';
  main.append(h('div.wizard-lead',
    help.note ? h('p', help.note) : null,
    h('div', { id: 'wizard-check' }, ...checklistNodes(step))));

  (WIZARD_BODY[step.id] || (() => []))().forEach((node) => node && main.append(node));

  main.append(h('div.wizard-foot', { id: 'wizard-foot' }, ...footNodes()));
  main.append(h('p.wizard-note',
    'Every change here is saved on its own within a second, and again whenever you move ' +
    'between steps. Close the app whenever you like — it reopens on this step.',
    h('button.link', {
      onclick: () => openModal('Leave setup?',
        'The guide closes and the full editor opens, with everything you have entered so ' +
        'far kept. You can come back with the "Setup guide" button at any time.',
        h('div'), () => finishWizard({})),
    }, 'Skip setup and open the full editor')));

  paintSaveState();
}
