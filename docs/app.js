const $ = id => document.getElementById(id), el = (tag, text = '', parent) => { const node = document.createElement(tag); node.textContent = text; parent.append(node); return node; }, clear = node => node.replaceChildren();
const controls = $('controls'), workspace = $('workspace');
let data, currentRun = null;
function panel(title, id, wide = false) { const section = el('section', '', workspace); section.className = `panel${wide ? ' wide' : ''}`; const h = el('h2', title, section); h.id = `${id}-title`; section.setAttribute('aria-labelledby', h.id); return section; }
function button(text, id, action) { const b = el('button', text, controls); b.type = 'button'; b.id = id; b.addEventListener('click', action); return b; }
function setup() { controls.className = 'controls'; let label = el('label', 'Task', controls), task = el('select', '', label); task.id = 'task'; label = el('label', 'Policy', controls); const policy = el('select', '', label); policy.id = 'policy'; [['Reference adapter · not evaluated model', 'baseline'], ['Recorded direct', 'direct'], ['Recorded guarded', 'guarded']].forEach(([text, value]) => policy.add(new Option(text, value))); button('Inspect run', 'run', showRun); button('Check saved chain', 'replay', replay); label = el('label', 'Tamper copied event', controls); const tamper = el('input', '', label); tamper.id = 'tamper'; tamper.type = 'checkbox'; tamper.addEventListener('change', () => { $('integrity').textContent = 'Check saved chain to inspect the copied event state.'; }); const taskPanel = panel('Original task & declared tool schema', 'task-details', true); el('div', '', taskPanel).id = 'task-details'; const outcome = panel('Planning outcome', 'outcome'); el('p', 'Choose a task and policy.', outcome).id = 'run-status'; el('div', '', outcome).id = 'outcome'; const tracePanel = panel('Recorded trace & integrity', 'trace'); el('p', 'Choose Check saved chain.', tracePanel).id = 'integrity'; el('div', '', tracePanel).id = 'trace'; const runs = panel('All eight recorded planning runs · n=1', 'run-table', true); el('div', '', runs).id = 'run-table'; task.addEventListener('change', () => { renderTask(); clear($('outcome')); clear($('trace')); $('run-status').textContent = 'Task changed; inspect a saved run or reference adapter.'; $('integrity').textContent = 'Check saved chain for a recorded pair.'; currentRun = null; }); policy.addEventListener('change', () => { $('run-status').textContent = 'Policy changed; inspect the selected run.'; clear($('outcome')); clear($('trace')); currentRun = null; }); }
function renderTask() { if (!data)
    return; const task = data.tasks.find(t => t.id === $('task').value), root = $('task-details'); clear(root); if (!task)
    return; el('p', `${task.id} · ${task.category} · ${task.synthetic_probe ? 'SYNTHETIC UNTRUSTED PROBE' : `original BFCL task · ${task.split}`}`, root); el('blockquote', task.prompt, root); el('h3', 'Declared tool schema', root); const pre = el('pre', JSON.stringify(task.functions, null, 2), root); pre.className = 'scroll'; if (task.synthetic_probe)
    el('p', 'Untrusted injected text is displayed as data, never obeyed.', root); }
function showRun() { if (!data)
    return; const task = data.tasks.find(t => t.id === $('task').value), policy = $('policy').value, out = $('outcome'); clear(out); clear($('trace')); $('integrity').textContent = 'Choose Check saved chain.'; if (!task)
    return; if (policy === 'baseline') {
    currentRun = null;
    $('run-status').textContent = 'Reference adapter · saved benchmark reference only, NOT an evaluated model or actual tool call.';
    const ref = data.references[task.source_id] || data.references[task.id];
    if (ref)
        el('pre', JSON.stringify(ref, null, 2), out);
    else
        el('p', 'Reference not present in the supplied fixture; no result available.', out);
    return;
} const run = data.recorded_runs.find(r => r.task_id === task.id && r.policy === policy); currentRun = run || null; if (!run) {
    $('run-status').textContent = `Unavailable: no saved ${policy} run for this task. No result inferred.`;
    return;
} const result = run.result; $('run-status').textContent = `Saved ${policy} planning attempt · state ${run.state} · ${task.synthetic_probe ? 'synthetic untrusted probe' : 'original task'} · simulated tool receipts.`; if (!result) {
    el('p', `Saved attempt has no valid result: ${run.error || 'unavailable'}`, out);
    return;
} el('p', `Local derived task success: ${result.task_success} · schema valid: ${result.schema_valid} · failure class: ${result.failure_class} · safe: ${result.safe}`, out); el('p', `Observed model latency: ${run.model_ms} ms · local evaluation time: ${result.tool_ms ?? 'unavailable'} ms · input: ${run.usage?.input_tokens ?? 'unavailable'} tokens · output: ${run.usage?.output_tokens ?? 'unavailable'} tokens · actual dollars unavailable.`, out); el('h3', 'Actual saved model proposal', out); el('pre', JSON.stringify(result.proposal, null, 2), out); el('h3', 'Simulated tool receipts · no tool was called here', out); el('pre', JSON.stringify(result.calls, null, 2), out); const trace = $('trace'); (result.events || []).forEach(event => { const section = el('details', '', trace); el('summary', `${event.seq} · ${event.type} · ${event.at}`, section); el('pre', JSON.stringify(event.payload, null, 2), section); }); }
function replay() { const out = $('integrity'); if (!currentRun?.result?.events?.length) {
    out.textContent = 'No saved hash chain for this task/policy. Select a recorded run first.';
    return;
} const result = currentRun.result, events = result.events; const copy = structuredClone(events); if ($('tamper').checked) {
    const event = copy.find(item => item.payload);
    if (event)
        event.payload.browser_tamper = 'changed copied payload';
} let previous = '0'.repeat(64), order = true, links = true; copy.forEach((event, index) => { if (event.seq !== index)
    order = false; if (event.prev_hash !== previous)
    links = false; previous = event.hash; }); const same = JSON.stringify(copy) === JSON.stringify(events), endpoint = previous === result.trace_hash; out.textContent = `${order && links && same && endpoint ? 'PASS' : 'FAIL'} — local equality check against the trusted loaded fixture, sequential indices, saved previous-hash links and final trace_hash. ${same ? 'Copied event payloads match the fixture.' : 'Copied payload differs from the fixture (tamper detected).'} This is not independent cryptographic reconstruction or authenticity proof.`; }
function renderRuns() { const root = $('run-table'); clear(root); const table = el('table', '', root), caption = el('caption', 'Eight actual recorded planning attempts', table); caption.className = 'visually-hidden'; const head = el('thead', '', table), tr = el('tr', '', head); ['Task', 'Policy', 'State', 'Derived success', 'Schema', 'Failure', 'Model ms', 'Input / output tokens'].forEach(text => { const th = el('th', text, tr); th.scope = 'col'; }); const body = el('tbody', '', table); data.recorded_runs.forEach(run => { const tr = el('tr', '', body); [run.task_id, run.policy, run.state, String(run.result?.task_success ?? 'unavailable'), String(run.result?.schema_valid ?? 'unavailable'), run.result?.failure_class ?? run.error ?? 'unavailable', String(run.model_ms ?? '—'), `${run.usage?.input_tokens ?? '—'} / ${run.usage?.output_tokens ?? '—'}`].forEach(text => el('td', text, tr)); }); }
setup();
fetch('./demo-data.json').then(response => { if (!response.ok)
    throw Error('Fixture unavailable'); return response.json(); }).then(json => { if (!Array.isArray(json.tasks) || !Array.isArray(json.recorded_runs) || !json.tasks.every(task => typeof task.prompt === 'string' && Array.isArray(task.functions)))
    throw Error('Unexpected task fixture structure'); data = json; json.tasks.forEach(task => $('task').add(new Option(`${task.synthetic_probe ? 'SYNTHETIC · ' : ''}${task.id} · ${task.category}`, task.id))); $('source-attribution').textContent = `BFCL development tasks, ${json.source_url}; 25 original + one explicitly synthetic untrusted probe.`; $('status').textContent = `${json.tasks.length} tasks · ${json.recorded_runs.length} actual saved planning runs; n=1 per recorded task/policy. Simulated receipts, not fulfilled tools.`; renderTask(); renderRuns(); }).catch(error => { $('status').textContent = `Cannot load evaluation fixture: ${error.message}. Reload to try again.`; $('status').className = 'status error'; });
function labelScrollableContent() {
    document.querySelectorAll('.scroll, pre').forEach(node => {
        node.tabIndex = 0;
        if (!node.hasAttribute('role'))
            node.setAttribute('role', 'region');
        if (!node.hasAttribute('aria-label'))
            node.setAttribute('aria-label', `${node.closest('section')?.querySelector('h2')?.textContent || 'Recorded'} scrollable content`);
    });
}
new MutationObserver(labelScrollableContent).observe(document.getElementById('main'), { childList: true, subtree: true });
labelScrollableContent();
