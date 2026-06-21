import streamlit as st
import re
import logging
import zipfile
import gzip
import io

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
@st.cache_data(max_entries=4, show_spinner="Parsing log streams...")
def scan_trace_linearly(file_content):
    raw_lines = file_content.splitlines()
    processed_lines = []
    
    depth_regex = re.compile(r'\(depth\s+(\d+)\):')
    
    for idx, line in enumerate(raw_lines):
        if not line.strip(): continue
        
        display_text = line.split("Flow:")[-1] if "Flow:" in line else " " + line.strip()
        display_text = display_text.strip()
        
        depth_match = depth_regex.search(line)
        depth = int(depth_match.group(1)) if depth_match else 0
        
        is_call = "-->" in line or "-->>" in line
        is_return = "<--" in line or "<<--" in line
        
        processed_lines.append({
            "idx": idx,
            "text": display_text,
            "raw": line,
            "depth": depth,
            "is_call": is_call,
            "is_return": is_return
        })
    return processed_lines

# --- REFINED STATE-DRIVEN EXPLORER ---
def render_interactive_explorer(lines, active_keywords, key_prefix, state_key):
    focus_stack = st.session_state[state_key]
    
    # 1. INITIAL ANCHORING
    if not focus_stack:
        for row in lines:
            if row["is_call"]:
                # Evaluates multiple keywords: passes if any registered keyword matches the line
                matches_kw = not active_keywords or any(kw.lower() in row["raw"].lower() for kw in active_keywords)
                if matches_kw:
                    button_label = row["text"]
                    if st.button(button_label, key=f"root_{key_prefix}_{row['idx']}"):
                        focus_stack.append(row)
                        st.session_state[state_key] = focus_stack
                        st.rerun()
        return

    # 2. NAVIGATION & CONTEXT HEADER
    if st.button("⬅️ Back to Previous Level", key=f"back_{key_prefix}"):
        focus_stack.pop()
        st.session_state[state_key] = focus_stack
        st.rerun()

    current_anchor = focus_stack[-1]
    
    # 3. BOUNDARY SCANNING
    start_idx = current_anchor["idx"]
    anchor_depth = current_anchor["depth"]
    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx]["is_return"] and lines[idx]["depth"] == anchor_depth:
            end_idx = idx
            break

    window_lines = lines[start_idx:end_idx + 1]
    
    # 4. VISUAL HIERARCHY RENDERING
    st.markdown(f"""
        <div style='background:#1E293B; padding:15px; border-radius:8px; border-top: 4px solid #10B981;'>
            <code style='color:#34D399;'>-->> (depth {anchor_depth})</code><br>
            <b>{current_anchor['text']}</b>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown("<br><b>Inner Execution Layers:</b>", unsafe_allow_html=True)
    
    found_children = False
    for row in window_lines:
        if row["depth"] == anchor_depth + 1:
            found_children = True
            if row["is_call"]:
                button_label = row["text"]
                if st.button(button_label, key=f"btn_{key_prefix}_{row['idx']}"):
                    focus_stack.append(row)
                    st.session_state[state_key] = focus_stack
                    st.rerun()
            elif row["is_return"]:
                st.markdown(f"<div class='return-line' style='margin-left:20px;'>{row['text']}</div>", unsafe_allow_html=True)

    if not found_children:
        st.info("No nested function calls found within this depth layer.")

    if end_idx < len(lines):
        st.markdown(f"<div class='return-line' style='border-top: 1px dashed #38BDF8; margin-top:10px;'>{lines[end_idx]['text']}</div>", unsafe_allow_html=True)
        
# --- ARCHIVE DECOMPRESSION UTILITY ---
def process_uploaded_file(uploaded_file):
    if uploaded_file is None: return ""
    name = uploaded_file.name
    bytes_data = uploaded_file.read()
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
    kw_html = "".join([f"<span class='keyword-badge'>{kw}</span>" for kw in st.session_state['trace_keywords']])
    st.sidebar.markdown(f"<div class='registry-box'>{kw_html}</div>", unsafe_allow_html=True)
else:
    st.sidebar.info("Displaying full raw streams (No active keywords).")

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
    uploaded_succ = st.file_uploader("Drop working trace log...", type=allowed_formats, key="u_succ")
    if uploaded_succ: st.session_state['master_succ'] = process_uploaded_file(uploaded_succ)

with col_uploader_r:
    st.markdown("### 🔴 Defective Flow Case")
    uploaded_fail = st.file_uploader("Drop broken trace log...", type=allowed_formats, key="u_fail")
    if uploaded_fail: st.session_state['master_fail'] = process_uploaded_file(uploaded_fail)

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