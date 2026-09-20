import streamlit as st
import re
import logging
import zipfile
import gzip
import io
from html import escape
from difflib import SequenceMatcher

# Silence background noise
logging.getLogger("streamlit.runtime.scriptrunner.script_runner").setLevel(logging.ERROR)
st.set_page_config(layout="wide", page_title="Infor LN Precise Trace Explorer", page_icon="⚖️")

# --- MASTER LAYOUT & STYLING ---
st.markdown("""
    <style>
    [data-testid="stColumn"] { overflow-x: auto; min-width: 0; padding: 8px; }
    
    /* Make buttons look like natural structural log lines */
    .stButton > button {
        width: 100% !important;
        text-align: left !important;
        justify-content: flex-start !important;
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 13px !important;
        padding: 6px 12px !important;
        margin-bottom: 2px !important;
        background-color: #1E293B !important;
        color: #10B981 !important;
        border: 1px solid #334155 !important;
        border-left: 4px solid #10B981 !important;
        border-radius: 4px !important;
    }
    .stButton > button:hover {
        background-color: #2D3748 !important;
        color: #34D399 !important;
        border-color: #4A5568 !important;
    }
    
    /* Sidebar button layout corrections */
    [data-testid="stSidebar"] .stButton > button {
        text-align: center !important;
        justify-content: center !important;
        font-family: inherit !important;
        font-size: 14px !important;
        border-left: 1px solid #334155 !important;
        background-color: transparent !important;
        color: inherit !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: #1E293B !important;
        color: #38BDF8 !important;
    }
    
    .return-line {
        font-family: 'Courier New', Courier, monospace;
        font-size: 13px;
        white-space: pre;
        color: #38BDF8;
        background-color: #0C4A6E;
        padding: 6px 12px;
        margin-bottom: 4px;
        border-radius: 4px;
        border-left: 4px solid #38BDF8;
    }
    .focus-banner {
        background-color: #0F172A;
        padding: 10px;
        border-radius: 6px;
        border: 1px solid #1E293B;
        margin-bottom: 12px;
    }
    .registry-box {
        background-color: #1E293B;
        padding: 10px;
        border-radius: 6px;
        border: 1px solid #334155;
        margin-bottom: 12px;
    }
    .keyword-badge {
        display: inline-block;
        background-color: #38BDF8;
        color: #0F172A;
        padding: 2px 8px;
        border-radius: 4px;
        font-family: monospace;
        font-size: 12px;
        font-weight: bold;
        margin: 2px;
    }
    </style>
""", unsafe_allow_html=True)

st.title("⚖️ Infor LN State-Driven Trace Explorer")
st.caption("Click structural call rows directly to dynamically explore deep child layers with precise line indexing.")

# --- NAVIGATION HISTORY STACKS ---
if 'active_focus_succ' not in st.session_state: st.session_state['active_focus_succ'] = []
if 'active_focus_fail' not in st.session_state: st.session_state['active_focus_fail'] = []
if 'trace_keywords' not in st.session_state: st.session_state['trace_keywords'] = []

# --- HIGH-SPEED LINEAR SCANNER ---
@st.cache_data(max_entries=4, show_spinner="Parsing trace calls...")
def scan_trace_linearly(file_content):
    """Build per-process call relationships; positions never use file line numbers."""
    rows = []
    stacks = {}
    event_re = re.compile(r'Flow:\s*(-->>|<<--|-->|<--)\s*\(depth\s+(\d+)\):\s*([^\s(]+)\s*\(')
    process_re = re.compile(r':::\((\d+)\):')
    object_re = re.compile(r'\(in object\s+([^)]*)\)')

    def abandon(pos, reason):
        rows[pos]['status'] = reason

    for line_number, raw in enumerate(file_content.splitlines(), 1):
        # Explicit trace discontinuities invalidate currently open calls.
        if re.search(r'<<\s*(?:Pausing|Restarted|Resumed) trace\s*>>', raw, re.I):
            for stack in stacks.values():
                for pos in stack:
                    abandon(pos, 'Incomplete: trace was paused or restarted')
            stacks.clear()
        if not raw.strip():
            continue
        event = event_re.search(raw)
        process = process_re.search(raw)
        obj = object_re.search(raw)
        pos = len(rows)
        row = {
            'pos': pos, 'line_number': line_number,
            'text': raw.split('Flow:', 1)[-1].strip(), 'raw': raw,
            'pid': process.group(1) if process else None,
            'depth': int(event.group(2)) if event else None,
            'function': event.group(3) if event else None,
            'object': obj.group(1).strip() if obj else None,
            'is_call': bool(event and event.group(1) in ('-->>', '-->')),
            'is_return': bool(event and event.group(1) in ('<<--', '<--')),
            'parent': None, 'children': [], 'return_pos': None,
            'status': 'Not a structural event',
        }
        rows.append(row)
        if not event:
            continue
        if row['pid'] is None:
            row['status'] = 'Unmatched: no process ID'
            continue
        stack = stacks.setdefault(row['pid'], [])
        depth = row['depth']
        if row['is_call']:
            while stack and rows[stack[-1]]['depth'] >= depth:
                abandon(stack.pop(), 'Incomplete: another call replaced this depth')
            if stack and rows[stack[-1]]['depth'] == depth - 1:
                row['parent'] = stack[-1]
                rows[stack[-1]]['children'].append(pos)
            row['status'] = 'Incomplete: no matching return found'
            stack.append(pos)
        else:
            while stack and rows[stack[-1]]['depth'] > depth:
                abandon(stack.pop(), 'Incomplete: ancestor returned before this call')
            if not stack or rows[stack[-1]]['depth'] != depth:
                row['status'] = 'Unmatched return'
                continue
            call_pos = stack.pop()
            call = rows[call_pos]
            if (call['function'] == row['function']
                    and call['object'] is not None
                    and call['object'] == row['object']):
                call['return_pos'] = pos
                call['status'] = 'Complete'
                row['status'] = 'Matched return'
            else:
                abandon(call_pos, 'Incomplete: conflicting return at this depth')
                row['status'] = 'Unmatched return: function or object differs'
    return rows


def render_interactive_explorer(lines, active_keywords, key_prefix, state_key):
    """Navigation stores parsed positions and uses explicit child/return links."""
    focus_stack = st.session_state[state_key]
    if any(not isinstance(pos, int) or not 0 <= pos < len(lines) for pos in focus_stack):
        focus_stack = []
        st.session_state[state_key] = focus_stack

    def label(row):
        return f"Line {row['line_number']} | Process {row['pid'] or '?'} | {row['text']}"

    def return_line(row):
        st.markdown("<div class='return-line'>" + escape(label(row)) + "</div>",
                    unsafe_allow_html=True)

    if not focus_stack:
        matches = [r for r in lines if r['is_call'] and (
            not active_keywords or any(kw.casefold() in r['raw'].casefold() for kw in active_keywords))]
        st.caption(f"{len(matches):,} matching function calls")
        for row in matches:
            if st.button(label(row), key=f"root_{key_prefix}_{row['pos']}"):
                focus_stack.append(row['pos'])
                st.rerun()
        if not matches:
            st.info('No function calls match the current keywords.')
        return

    if st.button('Back to Previous Level', key=f'back_{key_prefix}'):
        focus_stack.pop()
        st.rerun()
    anchor = lines[focus_stack[-1]]
    st.markdown("<div class='focus-banner'><b>" + escape(label(anchor)) + "</b></div>",
                unsafe_allow_html=True)
    if anchor['status'] != 'Complete':
        st.warning(anchor['status'] + '. Only observed child calls are shown; no return is guessed.')
    st.markdown('**Inner Execution Layers:**')
    for child_pos in anchor['children']:
        child = lines[child_pos]
        if st.button(label(child), key=f"btn_{key_prefix}_{child_pos}"):
            focus_stack.append(child_pos)
            st.rerun()
        if child['return_pos'] is not None:
            return_line(lines[child['return_pos']])
        else:
            st.caption(f"Line {child['line_number']}: {child['status']}")
    if not anchor['children']:
        st.info('No directly nested calls were observed for this process and depth.')
    if anchor['return_pos'] is not None:
        st.markdown('**Selected call returns:**')
        return_line(lines[anchor['return_pos']])


def reset_panel(side):
    """Uploader callback: reset only the changed panel, including removal."""
    st.session_state.pop('flow_comparison', None)
    st.session_state['comparison_generation'] = st.session_state.get('comparison_generation', 0) + 1
    for key in (f'master_{side}', f'loaded_{side}'):
        st.session_state.pop(key, None)
    st.session_state[f'active_focus_{side}'] = []


def load_panel(upload, side):
    if upload is None:
        reset_panel(side)
        return
    # Callback invalidates this flag on each upload change. Decode only once.
    if not st.session_state.get(f'loaded_{side}', False):
        st.session_state[f'master_{side}'] = process_uploaded_file(upload)
        st.session_state[f'loaded_{side}'] = True


def flow_events(rows, root_pos):
    """Full selected subtree, with relative depths; no argument values in tokens."""
    root = rows[root_pos]
    events = []
    problems = []
    pending = [(root_pos, False)]
    while pending:
        pos, returning = pending.pop()
        call = rows[pos]
        depth = call['depth'] - root['depth']
        if returning:
            if call['return_pos'] is not None:
                ret = rows[call['return_pos']]
                events.append((('RETURN', depth, call['function']), ret['line_number']))
            continue
        events.append((('CALL', depth, call['function']), call['line_number']))
        if call['status'] != 'Complete':
            problems.append(f"Line {call['line_number']}: {call['status']}")
        pending.append((pos, True))
        pending.extend((child, False) for child in reversed(call['children']))
    # Do not certify a match if the bounded execution contains unlinked events.
    if root['return_pos'] is not None:
        linked = {line for _, line in events}
        for row in rows[root_pos:root['return_pos'] + 1]:
            if row['pid'] == root['pid'] and row['line_number'] not in linked:
                if row['is_call'] or row['is_return'] or re.search(r'Flow:\s*(?:-->>|<<--|-->|<--)', row['raw']):
                    problems.append(f"Line {row['line_number']}: structural event outside the reconstructed subtree")
    return events, problems


def compare_flows(left_rows, left_pos, right_rows, right_pos):
    left, lp = flow_events(left_rows, left_pos)
    right, rp = flow_events(right_rows, right_pos)
    lt = [e[0] for e in left]
    rt = [e[0] for e in right]
    same = lt == rt
    # Bound expensive alignment without truncating or falsely declaring a match.
    if not same and max(len(lt), len(rt)) > 4000:
        return {'limited': True, 'left_count': len(lt), 'right_count': len(rt), 'problems': lp + rp}
    opcodes = [('equal', 0, len(lt), 0, len(rt))] if same else SequenceMatcher(None, lt, rt, autojunk=False).get_opcodes()
    result_rows = []
    for tag, a, b, c, d in opcodes:
        status = {'equal': 'Match', 'delete': 'Left only', 'insert': 'Right only',
                  'replace': 'Different structure'}[tag]
        for offset in range(max(b-a, d-c)):
            le = left[a+offset] if a+offset < b else None
            revent = right[c+offset] if c+offset < d else None
            def describe(event):
                if event is None:
                    return ''
                (kind, depth, function), line = event
                return f"{'  ' * depth}{kind} {function} [relative depth {depth}]"
            result_rows.append({'Status': status,
                                'Left line': le[1] if le else None,
                                'Left flow': describe(le),
                                'Right line': revent[1] if revent else None,
                                'Right flow': describe(revent)})
    return {'limited': False, 'same': same, 'left_count': len(left), 'right_count': len(right),
            'problems': ['Left: '+p for p in lp] + ['Right: '+p for p in rp], 'rows': result_rows}


def render_flow_comparison(left_rows, right_rows):
    st.subheader('Compare selected flows')
    st.caption('Compares all nested calls and returns by function, order, and relative depth. Arguments, timestamps, object names and cross-file process IDs are ignored.')
    lh = st.session_state.get('active_focus_succ', [])
    rh = st.session_state.get('active_focus_fail', [])
    if not lh or not rh:
        st.session_state.pop('flow_comparison', None)
        st.info('Open one function occurrence in each panel, then compare them.')
        return
    left, right = left_rows[lh[-1]], right_rows[rh[-1]]
    signature = (st.session_state.get('comparison_generation', 0), lh[-1], rh[-1])
    st.write(f"Left: {left['function']} · line {left['line_number']} · process {left['pid']}  |  "
             f"Right: {right['function']} · line {right['line_number']} · process {right['pid']}")
    saved = st.session_state.get('flow_comparison')
    if saved and saved['signature'] != signature:
        st.session_state.pop('flow_comparison', None)
        saved = None
    if st.button('Compare selected flows', key='compare_flows', type='primary'):
        with st.spinner('Comparing selected execution flows...'):
            result = compare_flows(left_rows, lh[-1], right_rows, rh[-1])
        saved = {'signature': signature, 'result': result}
        st.session_state.flow_comparison = saved
        st.session_state.difference_index = 0
        st.session_state.comparison_page = 1
    if not saved:
        return
    result = saved['result']
    if result['limited']:
        st.warning('These flows differ, but detailed alignment exceeds the 4,000-event limit. Select smaller child functions in both panels. No events were silently omitted.')
        st.write(f"Left: {result['left_count']:,} events; right: {result['right_count']:,} events")
        return
    if result['problems']:
        st.warning('Incomplete trace: a complete flow match cannot be confirmed. Differences below describe only the observed events.')
        with st.expander('Incomplete or unlinked events'):
            st.text('\n'.join(result['problems']))
    elif result['same']:
        st.success('Selected flows match: same functions, order, and nesting.')
    else:
        st.warning('Selected flows differ.')
    if result['same'] and result['problems']:
        st.info('The observed event sequences match, but at least one selected flow is incomplete.')
    rows = result['rows']
    differences = [i for i, row in enumerate(rows) if row['Status'] != 'Match']
    st.caption(f"Left: {result['left_count']:,} events · Right: {result['right_count']:,} events · {len(differences):,} differing aligned rows")
    if differences:
        prev, nxt = st.columns(2)
        if prev.button('Previous difference', key='previous_difference'):
            st.session_state.difference_index = (st.session_state.get('difference_index', 0)-1) % len(differences)
        if nxt.button('Next difference', key='next_difference'):
            st.session_state.difference_index = (st.session_state.get('difference_index', 0)+1) % len(differences)
        number = st.session_state.get('difference_index', 0) % len(differences)
        row = rows[differences[number]]
        st.markdown(f"**Difference {number+1} of {len(differences)} — {row['Status']}**")
        lcol, rcol = st.columns(2)
        lcol.code(f"Line {row['Left line']}\n{row['Left flow']}" if row['Left flow'] else 'No corresponding event', language=None)
        rcol.code(f"Line {row['Right line']}\n{row['Right flow']}" if row['Right flow'] else 'No corresponding event', language=None)
    only = st.checkbox('Show differences only', value=True, key='differences_only')
    visible = [rows[i] for i in differences] if only else rows
    if visible:
        pages = (len(visible)+99)//100
        if st.session_state.get('comparison_page', 1) > pages:
            st.session_state.comparison_page = 1
        page = st.number_input('Results page', min_value=1, max_value=pages, step=1, key='comparison_page')
        st.dataframe(visible[(page-1)*100:page*100], use_container_width=True)
    else:
        st.info('No differing rows to display.')
    st.caption('Alignment preserves repeated calls. A reordered call may appear as left-only and right-only events; these are alignment positions, not proof that the function is absent from the whole trace.')



# --- ARCHIVE DECOMPRESSION UTILITY ---
def process_uploaded_file(uploaded_file):
    if uploaded_file is None: return ""
    name = uploaded_file.name
    bytes_data = uploaded_file.getvalue()
    try:
        if name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(bytes_data)) as z:
                txts = [f for f in z.namelist() if f.endswith('.txt') or f.endswith('.log') or '.' not in f]
                if not txts: return ""
                with z.open(txts[0]) as f: return f.read().decode("utf-8", errors="ignore")
        elif name.endswith('.gz'):
            with gzip.GzipFile(fileobj=io.BytesIO(bytes_data)) as g: return g.read().decode("utf-8", errors="ignore")
        else: return bytes_data.decode("utf-8", errors="ignore")
    except Exception as e:
        st.error(f"Error unarchiving: {str(e)}")
        return ""

# --- SIDEBAR CONTROL HUB ---
st.sidebar.header("🛠️ Workspace Controls")
if st.sidebar.button("Core Workspace Reset / Clear All"):
    st.session_state.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Display Filters")

st.sidebar.markdown("**🔍 Multi-Keyword Filter Registry**")
new_keyword = st.sidebar.text_input("Enter Keyword to Filter Trees", "").strip()

col_kw_btn1, col_kw_btn2 = st.sidebar.columns(2)
with col_kw_btn1:
    if st.button("➕ Add Keyword"):
        if new_keyword and new_keyword not in st.session_state['trace_keywords']:
            st.session_state['trace_keywords'].append(new_keyword)
            st.rerun()
with col_kw_btn2:
    if st.button("🗑️ Clear Keywords"):
        st.session_state['trace_keywords'] = []
        st.session_state['active_focus_succ'] = []
        st.session_state['active_focus_fail'] = []
        st.rerun()

if st.session_state['trace_keywords']:
    kw_html = "".join([f"<span class='keyword-badge'>{escape(kw)}</span>" for kw in st.session_state['trace_keywords']])
    st.sidebar.markdown(f"<div class='registry-box'>{kw_html}</div>", unsafe_allow_html=True)
else:
    st.sidebar.info("Showing all function calls — no keyword filter applied.")

# Added: Explicit Search Execution Trigger Button
if st.sidebar.button("🔍 Search", type="primary"):
    st.session_state['active_focus_succ'] = []
    st.session_state['active_focus_fail'] = []
    st.rerun()

# --- FILE ARCHIVE UPLOADER LAYOUT ---
st.write("---")
col_uploader_l, col_uploader_r = st.columns(2)
allowed_formats = ["txt", "gz", "zip", "log"]

with col_uploader_l:
    st.markdown("### 🟢 Stable Flow Case")
    uploaded_succ = st.file_uploader("Drop working trace log...", type=allowed_formats, key="u_succ", on_change=reset_panel, args=("succ",))
    load_panel(uploaded_succ, "succ")

with col_uploader_r:
    st.markdown("### 🔴 Defective Flow Case")
    uploaded_fail = st.file_uploader("Drop broken trace log...", type=allowed_formats, key="u_fail", on_change=reset_panel, args=("fail",))
    load_panel(uploaded_fail, "fail")

trace_succ_raw = st.session_state.get('master_succ', '')
trace_fail_raw = st.session_state.get('master_fail', '')

# --- RUN STATE VIEWPORTS ---
if trace_succ_raw or trace_fail_raw:
    st.write("---")
    panel_left, panel_right = st.columns(2)
    keywords = st.session_state['trace_keywords']
    
    with panel_left:
        st.markdown("### 🟢 Stable Tree Workspace")
        if trace_succ_raw:
            data_succ = scan_trace_linearly(trace_succ_raw)
            render_interactive_explorer(data_succ, keywords, "succ", "active_focus_succ")
        else:
            st.info("Awaiting structural baseline input.")

    with panel_right:
        st.markdown("### 🔴 Defective Tree Workspace")
        if trace_fail_raw:
            data_fail = scan_trace_linearly(trace_fail_raw)
            render_interactive_explorer(data_fail, keywords, "fail", "active_focus_fail")
        else:
            st.info("Awaiting defective log data input.")

if trace_succ_raw and trace_fail_raw:
    st.divider()
    render_flow_comparison(data_succ, data_fail)
