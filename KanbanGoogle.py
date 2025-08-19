import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime
import hashlib
import plotly.express as px
import base64
from io import BytesIO
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURACIÓN GOOGLE SHEETS ---
SHEET_NAME = "kanban_backend"
CREDENTIALS_FILE = "credenciales.json"

@st.cache_resource
def get_gsheet_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    # Usamos las credenciales desde st.secrets
    creds_dict = st.secrets["gcp_service_account"]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME)


def ensure_worksheets_exist():
    """Verifica y crea las hojas necesarias si no existen"""
    try:
        sheet = get_gsheet_connection()
        required_sheets = ["tasks", "task_collaborators", "task_interactions", "users"]

        existing_sheets = [ws.title for ws in sheet.worksheets()]

        for sheet_name in required_sheets:
            if sheet_name not in existing_sheets:
                # Crear la hoja solo si no existe
                new_worksheet = sheet.add_worksheet(title=sheet_name, rows=100, cols=20)

                # Añadir encabezados según la hoja
                if sheet_name == "tasks":
                    new_worksheet.update('A1', [['id', 'task', 'description', 'date', 'priority',
                                               'shift', 'start_date', 'due_date', 'status',
                                               'completion_date', 'progress']])
                elif sheet_name == "task_collaborators":
                    new_worksheet.update('A1', [['task_id', 'username']])
                elif sheet_name == "task_interactions":
                    new_worksheet.update('A1', [['id', 'task_id', 'username', 'action_type',
                                               'timestamp', 'comment_text', 'image_base64',
                                               'new_status', 'progress_value']])
                elif sheet_name == "users":
                    new_worksheet.update('A1', [['username', 'password_hash', 'role']])

                st.success(f"Hoja '{sheet_name}' creada automáticamente")
    except Exception as e:
        st.error(f"Error al verificar hojas: {str(e)}")



# --- FUNCIONES DE AUTENTICACIÓN MEJORADAS ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_data(username):
    """Obtiene los datos de usuario normalizando nombres de columnas"""
    try:
        sheet = get_gsheet_connection()
        ws_users = sheet.worksheet("users")
        df_users = get_as_dataframe(ws_users)

        # Limpiar y normalizar datos
        df_users = df_users.dropna(how='all')
        df_users.columns = df_users.columns.str.strip().str.lower()

        # Buscar usuario (insensible a mayúsculas y eliminando espacios)
        user_row = df_users[
            df_users['username'].str.strip().str.lower() == username.strip().lower()
        ]

        if not user_row.empty:
            return user_row.iloc[0].to_dict()
        return None
    except Exception as e:
        st.error(f"Error al cargar datos de usuario: {e}")
        return None

def login_user(username, password):
    """Maneja el proceso de login con mensajes mejorados"""
    if not username or not password:
        st.error("Usuario y contraseña son requeridos")
        return False

    user_data = get_user_data(username)
    if not user_data:
        st.error("Usuario no encontrado")
        return False

    stored_hash = user_data.get('password_hash', '').strip()
    if not stored_hash:
        st.error("Credenciales inválidas")
        return False

    # Comparación segura de hashes
    provided_hash = hash_password(password)
    if hashlib.sha256(provided_hash.encode()).hexdigest() == hashlib.sha256(stored_hash.encode()).hexdigest():
        st.session_state.logged_in = True
        st.session_state.username = username
        st.session_state.current_role = user_data.get('role', 'Colaborador').strip()
        st.success(f"Bienvenido, {username}!")
        return True

    st.error("Contraseña incorrecta")
    return False

# --- FUNCIONES BACKEND KANBAN ---
def load_tasks_from_db():
    try:
        sheet = get_gsheet_connection()

        df_tasks_raw = get_as_dataframe(sheet.worksheet("tasks"))
        df_collab_raw = get_as_dataframe(sheet.worksheet("task_collaborators"))
        df_inter_raw = get_as_dataframe(sheet.worksheet("task_interactions"))

        # Filtrar filas vacías
        df_tasks = df_tasks_raw[df_tasks_raw.iloc[:, 0].notna()].copy() if not df_tasks_raw.empty else pd.DataFrame(columns=['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress'])
        df_collab = df_collab_raw[df_collab_raw.iloc[:, 0].notna()].copy() if not df_collab_raw.empty else pd.DataFrame(columns=['task_id', 'username'])
        df_inter = df_inter_raw[df_inter_raw.iloc[:, 0].notna()].copy() if not df_inter_raw.empty else pd.DataFrame(columns=['id', 'task_id', 'username', 'action_type', 'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value'])

        kanban_data = {
            "Por hacer": [],
            "En proceso": [],
            "Hecho": []
        }
        all_tasks_list = []

        if not df_tasks.empty:
            df_tasks['id'] = pd.to_numeric(df_tasks['id'], errors='coerce').fillna(0).astype(int)

            for _, row in df_tasks.iterrows():
                task = row.to_dict()
                task_id = int(task['id'])

                responsables = []
                if not df_collab.empty and 'task_id' in df_collab.columns:
                    df_collab['task_id'] = pd.to_numeric(df_collab['task_id'], errors='coerce').fillna(-1).astype(int)
                    responsables = df_collab[df_collab['task_id'] == task_id]['username'].tolist()
                task['responsible_list'] = responsables
                task['responsible'] = ", ".join(responsables)

                interacciones = []
                if not df_inter.empty and 'task_id' in df_inter.columns:
                    df_inter['task_id'] = pd.to_numeric(df_inter['task_id'], errors='coerce').fillna(-1).astype(int)
                    interacciones = df_inter[df_inter['task_id'] == task_id].to_dict('records')
                task['interactions'] = interacciones

                if task['status'] in kanban_data:
                    kanban_data[task['status']].append(task)
                else:
                    kanban_data["Por hacer"].append(task)

                all_tasks_list.append(task)

        st.session_state.kanban = kanban_data
        st.session_state.all_tasks_df = pd.DataFrame(all_tasks_list)

    except Exception as e:
        st.error(f"Error al cargar tareas: {e}")
        st.session_state.kanban = {
            "Por hacer": [],
            "En proceso": [],
            "Hecho": []
        }
        st.session_state.all_tasks_df = pd.DataFrame()

def add_task_to_db(task_data, initial_status, responsible_usernames):
    sheet = get_gsheet_connection()
    ws_tasks = sheet.worksheet("tasks")
    ws_collab = sheet.worksheet("task_collaborators")

    df_tasks = get_as_dataframe(ws_tasks)
    df_tasks = df_tasks[df_tasks.iloc[:, 0].notna()].copy() if not df_tasks.empty else pd.DataFrame(columns=['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress'])

    new_id = 1
    if not df_tasks.empty and 'id' in df_tasks.columns:
        df_tasks['id'] = pd.to_numeric(df_tasks['id'], errors='coerce').fillna(0).astype(int)
        new_id = int(df_tasks["id"].max() + 1)

    task_data['id'] = new_id
    task_data['status'] = initial_status
    task_data['completion_date'] = None
    task_data['progress'] = 0

    new_task_df = pd.DataFrame([task_data])

    existing_cols = df_tasks.columns.tolist()
    new_task_cols = new_task_df.columns.tolist()

    for col in existing_cols:
        if col not in new_task_cols:
            new_task_df[col] = None
    for col in new_task_cols:
        if col not in existing_cols:
            df_tasks[col] = None

    if not df_tasks.empty:
        new_task_df = new_task_df[df_tasks.columns]

    df_tasks = pd.concat([df_tasks, new_task_df], ignore_index=True)
    set_with_dataframe(ws_tasks, df_tasks)

    df_collab = get_as_dataframe(ws_collab)
    df_collab = df_collab[df_collab.iloc[:, 0].notna()].copy() if not df_collab.empty else pd.DataFrame(columns=['task_id', 'username'])

    new_collabs = pd.DataFrame([{"task_id": new_id, "username": u} for u in responsible_usernames])
    df_collab = pd.concat([df_collab, new_collabs], ignore_index=True)
    set_with_dataframe(ws_collab, df_collab)

    st.success("✅ Tarea agregada a Google Sheets.")
    load_tasks_from_db()

def update_task_status_in_db(task_id, new_status, completion_date=None, progress=None):
    sheet = get_gsheet_connection()
    ws = sheet.worksheet("tasks")
    df = get_as_dataframe(ws)
    df = df[df.iloc[:, 0].notna()].copy() if not df.empty else pd.DataFrame(columns=['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress'])

    mask = df["id"] == task_id
    if new_status:
        df.loc[mask, "status"] = new_status
    if completion_date:
        df.loc[mask, "completion_date"] = completion_date
    if progress is not None:
        df.loc[mask, "progress"] = progress

    set_with_dataframe(ws, df)
    st.success("✅ Estado de tarea actualizado en Google Sheets.")
    load_tasks_from_db()

def add_task_interaction(task_id, username, action_type, comment_text=None, image_base64=None, new_status=None, progress_value=None):
    sheet = get_gsheet_connection()
    ws = sheet.worksheet("task_interactions")
    df = get_as_dataframe(ws)
    df = df[df.iloc[:, 0].notna()].copy() if not df.empty else pd.DataFrame(columns=['id', 'task_id', 'username', 'action_type', 'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value'])

    new_id = 1
    if not df.empty and 'id' in df.columns:
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        new_id = int(df["id"].max() + 1)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_row = {
        "id": new_id,
        "task_id": task_id,
        "username": username,
        "action_type": action_type,
        "timestamp": timestamp,
        "comment_text": comment_text,
        "image_base64": image_base64,
        "new_status": new_status,
        "progress_value": progress_value
    }

    new_row_df = pd.DataFrame([new_row])
    if not df.empty:
        for col in df.columns:
            if col not in new_row_df.columns:
                new_row_df[col] = None
        new_row_df = new_row_df[df.columns]

    df = pd.concat([df, new_row_df], ignore_index=True)
    set_with_dataframe(ws, df)
    st.success("Interacción registrada en Google Sheets.")
    load_tasks_from_db()

def generate_excel_export():
    sheet = get_gsheet_connection()
    output = BytesIO()
    try:
        df_tasks = get_as_dataframe(sheet.worksheet("tasks"))
        df_collab = get_as_dataframe(sheet.worksheet("task_collaborators"))
        df_inter = get_as_dataframe(sheet.worksheet("task_interactions"))

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            if not df_tasks.empty and df_tasks.iloc[:,0].notna().any():
                df_tasks.to_excel(writer, sheet_name='Tareas', index=False)
            else:
                pd.DataFrame(columns=['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress']).to_excel(writer, sheet_name='Tareas', index=False)

            if not df_collab.empty and df_collab.iloc[:,0].notna().any():
                df_collab.to_excel(writer, sheet_name='Colaboradores', index=False)
            else:
                pd.DataFrame(columns=['task_id', 'username']).to_excel(writer, sheet_name='Colaboradores', index=False)

            if not df_inter.empty and df_inter.iloc[:,0].notna().any():
                df_inter.to_excel(writer, sheet_name='Interacciones', index=False)
            else:
                pd.DataFrame(columns=['id', 'task_id', 'username', 'action_type', 'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value']).to_excel(writer, sheet_name='Interacciones', index=False)

        output.seek(0)
        return output
    except Exception as e:
        st.error(f"Error al generar archivo: {str(e)}")
        return None

def clear_task_data_from_db():
    try:
        sheet = get_gsheet_connection()
        for ws_name in ["task_collaborators", "task_interactions", "tasks", "users"]:
            ws = sheet.worksheet(ws_name)
            ws.clear()
            if ws_name == "tasks":
                ws.update('A1', [['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress']])
            elif ws_name == "task_collaborators":
                ws.update('A1', [['task_id', 'username']])
            elif ws_name == "task_interactions":
                ws.update('A1', [['id', 'task_id', 'username', 'action_type', 'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value']])
            elif ws_name == "users":
                ws.update('A1', [['username', 'password_hash', 'role']])
        st.success("Google Sheet limpiado correctamente.")
    except Exception as e:
        st.error(f"Error al limpiar Google Sheet: {str(e)}")
    load_tasks_from_db()

def create_new_user_in_db(username, password, role):
    sheet = get_gsheet_connection()
    ws_users = sheet.worksheet("users")
    df_users = get_as_dataframe(ws_users)
    df_users = df_users[df_users.iloc[:, 0].notna()].copy() if not df_users.empty else pd.DataFrame(columns=['username', 'password_hash', 'role'])

    if not df_users.empty and username in df_users['username'].values:
        st.error(f"El usuario '{username}' ya existe.")
        return False

    hashed_password = hash_password(password)
    new_user = {"username": username, "password_hash": hashed_password, "role": role}
    new_user_df = pd.DataFrame([new_user])

    if not df_users.empty:
        for col in df_users.columns:
            if col not in new_user_df.columns:
                new_user_df[col] = None
        new_user_df = new_user_df[df_users.columns]

    df_users = pd.concat([df_users, new_user_df], ignore_index=True)
    set_with_dataframe(ws_users, df_users)
    st.success(f"Usuario '{username}' creado exitosamente con rol '{role}'.")
    return True

def update_user_password_in_db(username, new_password):
    sheet = get_gsheet_connection()
    ws_users = sheet.worksheet("users")
    df_users = get_as_dataframe(ws_users)
    df_users = df_users[df_users.iloc[:, 0].notna()].copy() if not df_users.empty else pd.DataFrame(columns=['username', 'password_hash', 'role'])

    mask = df_users["username"] == username
    if mask.any():
        df_users.loc[mask, "password_hash"] = hash_password(new_password)
        set_with_dataframe(ws_users, df_users)
        st.success(f"Contraseña para '{username}' actualizada exitosamente.")
        return True
    else:
        st.error(f"Usuario '{username}' no encontrado.")
        return False

def formatear_tarea_display(t):
    """Formatea los detalles de una tarea para visualización."""
    card_color = "#393E46"  # Color por defecto

    if t['status'] == 'Hecho':
        card_color = "#4CAF50"
    elif t['status'] in ['Por hacer', 'En proceso']:
        if t.get('due_date'):
            try:
                task_due_date = date.fromisoformat(str(t['due_date']))
                today = date.today()
                if task_due_date <= today:
                    card_color = "#F44336"
                elif task_due_date <= today + timedelta(days=3):
                    card_color = "#FFC107"
            except (ValueError, TypeError):
                pass

    description_html = f"<br><strong>📝 Descripción:</strong> {t['description']}" if t.get('description') else ""
    start_date_html = f"<br><strong>➡️ Inicio:</strong> {t['start_date']}" if t.get('start_date') else ""
    due_date_html = f"<br><strong>🔚 Término:</strong> {t['due_date']}" if t.get('due_date') else ""

    responsible_display = ", ".join(t.get('responsible_list', [])) or "Sin asignar"

    progress_val = t.get('progress', 0)
    progress_html = f"""
    <div style="width: 100%; background-color: #ddd; border-radius: 5px; margin-top: 8px; overflow: hidden;">
        <div style="width: {progress_val}%; background-color: #007bff; color: white; text-align: center; border-radius: 5px; padding: 2px 0;">
            {progress_val}%
        </div>
    </div>
    """

    card_html = f"""
    <div style="background-color:{card_color}; color:white; padding: 10px; border-radius: 5px; margin-bottom: 10px;">
        <strong>🔧 Tarea:</strong> {t['task']}
        {description_html}
        <br><strong>👷 Responsables:</strong> {responsible_display}
        <br><strong>📅 Creada:</strong> {t['date']}
        {start_date_html}
        {due_date_html}
        <br><strong>🧭 Turno:</strong> {t['shift']}
        <br><strong>🔥 Prioridad:</strong> {t['priority']}
        {progress_html}
    </div>
    """

    return {
        'card_html': card_html,
        'interactions': t.get('interactions', [])
    }

# --- INTERFAZ DE LA APLICACIÓN ---
def initialize_app():
    """Configura el estado inicial de la aplicación"""
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'current_role' not in st.session_state:
        st.session_state.current_role = None

    # Asegurar que las hojas existan antes de cargar datos
    ensure_worksheets_exist()

    if 'kanban' not in st.session_state:
        load_tasks_from_db()

def login_screen():
    """Muestra solo la pantalla de login hasta que se autentique"""
    st.set_page_config(page_title="Login - Sistema Kanban", layout="centered")
    st.title("Sistema Kanban")
    st.markdown("---")

    with st.container():
        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            with st.form("main_login_form"):
                st.subheader("Acceso al Sistema")
                username = st.text_input("Usuario")
                password = st.text_input("Contraseña", type="password")

                if st.form_submit_button("Ingresar"):
                    if login_user(username, password):
                        st.rerun()

            st.markdown("---")
            st.info("🔐 Credenciales de prueba:")
            st.code("Usuario: Admin Principal\nContraseña: admin")

def main_app():
    """Interfaz principal después del login"""
    st.set_page_config(page_title="Sistema Kanban", layout="wide")

    # Barra lateral con info de usuario
    with st.sidebar:
        if st.session_state.logged_in:
            st.write(f"👤 Usuario: **{st.session_state.username}**")
            st.write(f"🎚️ Rol: **{st.session_state.current_role}**")
            if st.button("Cerrar Sesión"):
                st.session_state.logged_in = False
                st.session_state.username = None
                st.session_state.current_role = None
                st.rerun()

    # Determinar roles admin (insensible a mayúsculas)
    admin_roles = ["admin principal", "supervisor", "coordinador"]
    is_admin = st.session_state.current_role.lower() in admin_roles

    # Definir pestañas
    tab_names = ["📋 Tablero Kanban"]
    if is_admin:
        tab_names.insert(0, "➕ Agregar Tarea")
        tab_names.append("📊 Estadísticas")
        tab_names.append("⚙️ Gestión Usuarios")

    tabs = st.tabs(tab_names)

    # --- Pestaña: Agregar Tarea (Solo admin) ---
    if is_admin and "➕ Agregar Tarea" in tab_names:
        with tabs[tab_names.index("➕ Agregar Tarea")]:
            st.header("➕ Agregar Nueva Tarea")
            st.markdown("---")

            with st.form("agregar_tarea"):
                tarea = st.text_input("Nombre de la Tarea*")
                description = st.text_area("Descripción de la Tarea (Opcional)")

                # Obtener usuarios para asignar
                sheet = get_gsheet_connection()
                df_users = get_as_dataframe(sheet.worksheet("users"))
                df_users = df_users[df_users.iloc[:, 0].notna()].copy() if not df_users.empty else pd.DataFrame()

                collab_users = []
                if not df_users.empty and 'role' in df_users.columns:
                    collab_users = df_users[~df_users['role'].str.lower().isin(["admin principal"])]['username'].tolist()
                collab_users.sort()

                responsables = st.multiselect("Seleccionar Responsables*", options=collab_users)

                fecha = st.date_input("Fecha de Creación*", date.today())
                fecha_inicial = st.date_input("Fecha Inicial (Opcional)", value=None)
                fecha_termino = st.date_input("Fecha Término (Opcional)", value=None)

                prioridad = st.selectbox("Prioridad*", ["Alta", "Media", "Baja"])
                turno = st.selectbox("Turno*", ["1er Turno", "2do Turno", "3er Turno"])
                destino = st.selectbox("Columna Inicial*", ["Por hacer", "En proceso"])

                submit = st.form_submit_button("Crear Tarea")

                if submit:
                    if not tarea:
                        st.error("El nombre de la tarea es obligatorio")
                    elif not responsables:
                        st.error("Debe asignar al menos un responsable")
                    else:
                        nueva_tarea = {
                            "task": tarea,
                            "description": description,
                            "date": fecha.strftime("%Y-%m-%d"),
                            "priority": prioridad,
                            "shift": turno,
                            "start_date": fecha_inicial.strftime("%Y-%m-%d") if fecha_inicial else None,
                            "due_date": fecha_termino.strftime("%Y-%m-%d") if fecha_termino else None
                        }
                        add_task_to_db(nueva_tarea, destino, responsables)

    # --- Pestaña: Tablero Kanban ---
    with tabs[tab_names.index("📋 Tablero Kanban")]:
        st.header("📋 Tablero Kanban")
        st.markdown("---")

        # Obtener lista de responsables para filtro
        all_responsibles = []
        for status_list in st.session_state.kanban.values():
            for task in status_list:
                if 'responsible_list' in task:
                    all_responsibles.extend(task['responsible_list'])

        responsables_unicos = sorted(list(set(all_responsibles)))

        # Seleccionar filtro (para colaboradores, mostrar solo sus tareas por defecto)
        default_idx = 0
        if st.session_state.current_role.lower() == "colaborador" and st.session_state.username in responsables_unicos:
            default_idx = responsables_unicos.index(st.session_state.username) + 1

        filtro_responsable = st.selectbox(
            "👤 Filtrar por responsable:",
            ["(Todos)"] + responsables_unicos,
            index=default_idx
        )

        # Columnas del Kanban
        cols = st.columns(3)
        estados = ["Por hacer", "En proceso", "Hecho"]

        for col, estado in zip(cols, estados):
            with col:
                st.markdown(f"### {estado}")

                # Filtrar tareas según estado y responsable seleccionado
                tareas_estado = st.session_state.kanban.get(estado, [])
                tareas_mostrar = [
                    t for t in tareas_estado
                    if filtro_responsable == "(Todos)" or filtro_responsable in t.get('responsible_list', [])
                ]

                if not tareas_mostrar:
                    st.info("No hay tareas en esta sección.")
                    continue

                for task in tareas_mostrar:
                    task_display = formatear_tarea_display(task)
                    st.markdown(task_display['card_html'], unsafe_allow_html=True)

                    if task_display['interactions']:
                        with st.expander(f"📝 Historial ({len(task_display['interactions'])})", expanded=False):
                            for interaccion in task_display['interactions']:
                                if interaccion.get('comment_text'):
                                    st.caption(f"💬 {interaccion.get('username', 'Usuario')} - {interaccion.get('timestamp', 'Fecha')}")
                                    st.info(interaccion['comment_text'])

                                if interaccion.get('image_base64'):
                                    st.caption("📸 Evidencia adjunta")
                                    try:
                                        st.image(base64.b64decode(interaccion['image_base64']), use_column_width=True)
                                    except:
                                        st.error("Error al cargar imagen")

                                st.markdown("---")

                    if estado in ['Por hacer', 'En proceso']:
                        current_username = st.session_state.get('username')
                        if is_admin or (current_username and current_username in task.get('responsible_list', [])):
                            with st.expander(f"✏️ Actualizar {task.get('task', 'tarea')}", expanded=False):
                                with st.form(key=f"update_task_form_{task['id']}"):
                                    progreso_actual = task.get('progress', 0)
                                    nuevo_progreso = st.slider(
                                        "Porcentaje de avance:",
                                        0, 100, int(progreso_actual), 5,
                                        key=f"progress_{task['id']}_form"
                                    )

                                    comentario = st.text_area(
                                        "Comentario:",
                                        key=f"comment_{task['id']}_form"
                                    )

                                    evidencia = st.file_uploader(
                                        "Subir evidencia (imagen):",
                                        type=["png", "jpg", "jpeg"],
                                        key=f"upload_{task['id']}_form"
                                    )

                                    col1_form, col2_form = st.columns(2)

                                    with col1_form:
                                        submit_avance = st.form_submit_button("Guardar avance", help="Guarda el progreso y el comentario.")

                                    with col2_form:
                                        submit_completar = st.form_submit_button("Marcar como completada", help="Marca la tarea como completada al 100%.")

                                    if submit_avance:
                                        imagen_b64 = None
                                        if evidencia:
                                            imagen_b64 = base64.b64encode(evidencia.getvalue()).decode('utf-8')

                                        update_task_status_in_db(
                                            task['id'],
                                            task['status'],
                                            progress=nuevo_progreso
                                        )

                                        add_task_interaction(
                                            task_id=task['id'],
                                            username=st.session_state.username,
                                            action_type='progress_update',
                                            comment_text=comentario,
                                            image_base64=imagen_b64,
                                            progress_value=nuevo_progreso
                                        )
                                        st.rerun()

                                    if submit_completar:
                                        imagen_b64 = None
                                        if evidencia:
                                            imagen_b64 = base64.b64encode(evidencia.getvalue()).decode('utf-8')

                                        update_task_status_in_db(
                                            task['id'],
                                            "Hecho",
                                            date.today().strftime("%Y-%m-%d"),
                                            progress=100
                                        )

                                        add_task_interaction(
                                            task_id=task['id'],
                                            username=st.session_state.username,
                                            action_type='status_change',
                                            comment_text=comentario,
                                            image_base64=imagen_b64,
                                            new_status="Hecho",
                                            progress_value=100
                                        )
                                        st.rerun()

    # --- Pestaña: Estadísticas (Solo admin) ---
    if is_admin and "📊 Estadísticas" in tab_names:
        with tabs[tab_names.index("📊 Estadísticas")]:
            st.header("📊 Estadísticas del Kanban")
            st.markdown("---")

            if st.session_state.all_tasks_df.empty:
                st.info("No hay datos de tareas para mostrar estadísticas.")
            else:
                df = st.session_state.all_tasks_df.copy()

                df['due_date'] = pd.to_datetime(df['due_date'], errors='coerce')
                df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
                df['date'] = pd.to_datetime(df['date'], errors='coerce')

                # Métricas clave
                st.subheader("Métricas Clave")

                total_tareas = len(df)
                por_hacer = len(df[df['status'] == 'Por hacer'])
                en_proceso = len(df[df['status'] == 'En proceso'])
                completadas = len(df[df['status'] == 'Hecho'])

                hoy = date.today()
                vencidas = len(df[(df['due_date'].notna()) & (df['due_date'].dt.date < hoy) & (df['status'] != 'Hecho')])
                por_vencer = len(df[(df['due_date'].notna()) &
                                     (df['due_date'].dt.date >= hoy) &
                                     (df['due_date'].dt.date <= hoy + timedelta(days=3)) &
                                     (df['status'] != 'Hecho')])

                col1, col2, col3, col4, col5, col6 = st.columns(6)

                with col1:
                    st.metric("📊 Tareas totales", total_tareas)

                with col2:
                    st.metric("🔄 Por Hacer", por_hacer)

                with col3:
                    st.metric("⚙️ En Progreso", en_proceso)

                with col4:
                    st.metric("✅ Completadas", completadas)

                with col5:
                    st.metric("⏰ Vencidas", vencidas)

                with col6:
                    st.metric("⚠️ Por Vencer", por_vencer)

                st.markdown("---")

                # Gráfico de estado de tareas
                st.subheader("Estado de Tareas (Vencimiento)")

                estado_data = {
                    'Categoría': ['Vencidas', 'Por Vencer', 'Completadas'],
                    'Cantidad': [vencidas, por_vencer, completadas]
                }
                df_estado = pd.DataFrame(estado_data)

                fig_barras = px.bar(
                    df_estado,
                    x='Categoría',
                    y='Cantidad',
                    color='Categoría',
                    color_discrete_map={
                        'Vencidas': '#F44336',
                        'Por Vencer': '#FFC107',
                        'Completadas': '#4CAF50'
                    },
                    text='Cantidad'
                )
                fig_barras.update_layout(showlegend=False)
                st.plotly_chart(fig_barras, use_container_width=True)

                st.markdown("---")

                # Distribución por estado
                st.subheader("Distribución de Tareas por Estado")

                estado_tareas_data = {
                    'Estado': ['Por hacer', 'En proceso', 'Hecho'],
                    'Cantidad': [por_hacer, en_proceso, completadas]
                }
                df_estado_tareas = pd.DataFrame(estado_tareas_data)

                fig_estado = px.bar(
                    df_estado_tareas,
                    x='Estado',
                    y='Cantidad',
                    color='Estado',
                    color_discrete_map={
                        'Por hacer': '#FF9800',
                        'En proceso': '#2196F3',
                        'Hecho': '#4CAF50'
                    },
                    text='Cantidad'
                )
                fig_estado.update_layout(showlegend=False)
                st.plotly_chart(fig_estado, use_container_width=True)

                st.markdown("---")

                # Avance por responsable
                st.subheader("Avance por Responsable")

                df_filtered_responsibles = df[df['responsible_list'].apply(lambda x: isinstance(x, list) and len(x) > 0)]

                if not df_filtered_responsibles.empty:
                    df_flat = df_filtered_responsibles.explode('responsible_list')
                    df_responsable = df_flat.groupby(['responsible_list', 'status']).size().unstack(fill_value=0)
                    df_responsable = df_responsable.reset_index().melt(id_vars='responsible_list',
                                                                        value_name='Cantidad',
                                                                        var_name='Estado')

                    fig_responsable = px.bar(
                        df_responsable,
                        x='responsible_list',
                        y='Cantidad',
                        color='Estado',
                        color_discrete_map={
                            'Por hacer': '#FF9800',
                            'En proceso': '#2196F3',
                            'Hecho': '#4CAF50'
                        },
                        barmode='group',
                        text='Cantidad'
                    )
                    fig_responsable.update_layout(xaxis_title='Responsable', yaxis_title='Cantidad de Tareas')
                    st.plotly_chart(fig_responsable, use_container_width=True)
                else:
                    st.warning("No hay datos de responsables asignados para mostrar el avance.")

                st.markdown("---")

                # Distribución por prioridad
                st.subheader("Distribución de Tareas por Prioridad")

                if 'priority' in df.columns:
                    prioridad_counts = df['priority'].value_counts().reset_index()
                    prioridad_counts.columns = ['Prioridad', 'Cantidad']

                    fig_prioridad = px.pie(
                        prioridad_counts,
                        values='Cantidad',
                        names='Prioridad',
                        hole=0.4,
                        color='Prioridad',
                        color_discrete_map={
                            'Alta': '#F44336',
                            'Media': '#FFC107',
                            'Baja': '#4CAF50'
                        }
                    )
                    fig_prioridad.update_traces(textposition='inside', textinfo='percent+label')
                    fig_prioridad.update_layout(showlegend=False)
                    st.plotly_chart(fig_prioridad, use_container_width=True)
                else:
                    st.warning("No hay datos de prioridad para mostrar.")

    # --- Pestaña: Gestión de Usuarios (Solo admin) ---
    if is_admin and "⚙️ Gestión Usuarios" in tab_names:
        with tabs[tab_names.index("⚙️ Gestión Usuarios")]:
            st.header("⚙️ Gestión de Usuarios")
            st.markdown("---")

            # Lista de usuarios existentes
            sheet = get_gsheet_connection()
            ws_users = sheet.worksheet("users")
            usuarios = get_as_dataframe(ws_users)
            usuarios = usuarios[usuarios.iloc[:, 0].notna()].copy() if not usuarios.empty else pd.DataFrame(columns=['username', 'password_hash', 'role'])

            if 'password_hash' in usuarios.columns:
                usuarios_display = usuarios[['username', 'role']].copy()
            else:
                usuarios_display = usuarios[['username', 'role']].copy()

            st.subheader("Usuarios Registrados")
            st.dataframe(usuarios_display)

            # Crear nuevo usuario
            st.markdown("---")
            st.subheader("Crear Nuevo Usuario")

            with st.form("nuevo_usuario"):
                nuevo_usuario = st.text_input("Nombre de usuario*")
                nueva_contraseña = st.text_input("Contraseña*", type="password")
                confirmar_contraseña = st.text_input("Confirmar contraseña*", type="password")
                rol = st.selectbox("Rol*", ["Admin Principal", "Supervisor", "Coordinador", "Colaborador"])

                if st.form_submit_button("Crear Usuario"):
                    if not nuevo_usuario or not nueva_contraseña or not confirmar_contraseña:
                        st.error("Todos los campos marcados con * son obligatorios")
                    elif nueva_contraseña != confirmar_contraseña:
                        st.error("Las contraseñas no coinciden")
                    else:
                        if create_new_user_in_db(nuevo_usuario, nueva_contraseña, rol):
                            st.rerun()

            # Cambiar contraseña
            st.markdown("---")
            st.subheader("Cambiar Contraseña")

            with st.form("cambiar_contraseña"):
                usuario_a_cambiar_pass = st.selectbox(
                    "Seleccionar usuario",
                    usuarios['username'].tolist() if not usuarios.empty else [],
                    key="select_user_pass_change"
                )
                nueva_contraseña_change = st.text_input("Nueva contraseña*", type="password", key="new_pass_change")
                confirmar_contraseña_change = st.text_input("Confirmar nueva contraseña*", type="password", key="confirm_pass_change")

                if st.form_submit_button("Actualizar Contraseña"):
                    if not nueva_contraseña_change or not confirmar_contraseña_change:
                        st.error("Todos los campos marcados con * son obligatorios")
                    elif nueva_contraseña_change != confirmar_contraseña_change:
                        st.error("Las contraseñas no coinciden")
                    else:
                        update_user_password_in_db(usuario_a_cambiar_pass, nueva_contraseña_change)
                        st.rerun()

            # Administración de la base de datos
            st.markdown("---")
            st.subheader("Administración de Base de Datos")

            # with st.form("export_data_form"):
            #     st.info("Haz clic para descargar una copia de seguridad de todos los datos del Kanban en formato Excel.")
            #     if st.form_submit_button("📤 Exportar datos a Excel"):
            #         archivo = generate_excel_export()
            #         if archivo:
            #             st.download_button(
            #                 label="⬇️ Descargar archivo Excel",
            #                 data=archivo,
            #                 file_name=f"backup_kanban_{date.today()}.xlsx",
            #                 mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            #             )

            with st.form("clear_data_form"):
                st.markdown("---")
                st.warning("Zona de peligro - Estas acciones no se pueden deshacer")
                confirmar = st.checkbox("Entiendo que esta acción borrará todos los datos de tareas y usuarios", key="confirm_clear_data")
                if st.form_submit_button("⚠️ Limpiar Base de Datos", type="primary"):
                    if confirmar:
                        clear_task_data_from_db()
                        st.rerun()
                    else:
                        st.error("Debe confirmar que entiende esta acción para continuar.")

# --- FLUJO PRINCIPAL DE LA APLICACIÓN ---
initialize_app()

if not st.session_state.logged_in:
    login_screen()
else:
    main_app()
