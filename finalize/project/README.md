# Smart Maintenance Tracker

> Built for **Problem Statement 21 (Manufacturing Maintenance & Downtime Tracker)** — CBC 2.0 (Software & Web Development).

The **Smart Maintenance Tracker** is an end-to-end manufacturing maintenance management web application that prioritizes maintenance work orders dynamically, tracks equipment downtime in real time, automates technician assignments based on skills and workload, and computes MTTR, MTBF, and financial downtime losses.

---

## 🛠️ Technology Stack

- **Frontend**: React 18, TypeScript, Tailwind CSS, Lucide Icons, Vite
- **Backend**: Python 3.14, Flask, Flask-JWT-Extended, Flask-CORS, APScheduler, bcrypt
- **Database**: MySQL (phpMyAdmin / XAMPP)

---

## 🚀 Quick Start Guide

### Step 1: Start MySQL in XAMPP
1. Open the **XAMPP Control Panel**.
2. Click **Start** on **Apache** and **MySQL**.
3. Open your browser and navigate to phpMyAdmin: [http://localhost/phpmyadmin](http://localhost/phpmyadmin)

---

### Step 2: Import Database Schema into phpMyAdmin
1. In phpMyAdmin, click the **Import** tab at the top.
2. Click **Choose File** and select:
   ```
   backend/database/schema.sql
   ```
3. Click **Go** at the bottom.
4. This creates the database `smart_maintenance_tracker` with all 13 required tables:
   - `users`, `skills`, `technicians`, `technician_skills`, `equipment`
   - `priority_rules`, `maintenance_schedules`, `maintenance_requests`
   - `assignments`, `downtime_logs`, `status_history`, `notifications`, `request_comments`

---

### Step 3: Seed Demo Data
In your terminal, navigate to the `backend` folder and run the seeder script:

```bash
cd backend
python create_demo_data.py
```

This creates:
- **4 User Accounts** with secure bcrypt password hashing
- **3 Technicians** with skills (Mechanical, Electrical, Hydraulic), zones, and shifts
- **8 Production Machines** across Lines 1, 2, and 3
- **5 Preventive Maintenance Schedules**
- **5 Demo Maintenance Requests** covering priority levels P1 to P4 with audit logs and downtime records

---

### Step 4: Run Flask Backend
In the `backend` folder:

```bash
python app.py
```

The Flask API will start on: **`http://localhost:5000`**
- Includes JWT authentication (`/api/auth/login`)
- Dynamic priority calculation engine (`priority_engine.py`)
- Automated skill-matched technician assignment engine (`assignment_engine.py`)
- Background scheduler for preventive maintenance and SLA monitoring (`scheduler.py`)

---

### Step 5: Run React Frontend
In a new terminal window in the project root:

```bash
.\node_modules\.bin\vite.cmd
# or
npm run dev
```

Open your browser at: **`http://localhost:5173`**

---

## 👥 Demo User Credentials

The login screen features **1-click quick-fill buttons** for each role:

| Role | Email | Password | Allowed Access |
|---|---|---|---|
| **Operator** | `operator@factory.com` | `Op@123` | Report Breakdowns, View Equipment & Own Requests |
| **Technician** | `tech1@factory.com` | `Tech@123` | My Tasks, Start/Hold/Complete Work, Equipment View |
| **Supervisor** | `supervisor@factory.com` | `Sup@123` | Dashboard, Priority Queue, Assignment Board, Schedules, Analytics |
| **Administrator** | `admin@factory.com` | `Admin@123` | All features + User Management, Equipment Admin, Priority Rules Config |

---

## 🌟 Key Features Implemented (Per SRS)

1. **Dynamic Priority Scoring Engine (FR-18 to FR-21)**:
   $$\text{Priority Score} = (C \times U) + P + S + \min(W, 10) + (D \times 2)$$
   - $C$: Equipment Criticality (1–5)
   - $U$: Urgency Level (1–4)
   - $P$: Production Stopped (+10)
   - $S$: Safety Risk (+10)
   - $W$: Waiting time in hours (max 10)
   - $D$: Days overdue for preventive tasks ($D \times 2$)
   - Automated thresholding: $\ge 30 \implies \text{P1}$, $\ge 20 \implies \text{P2}$, $\ge 10 \implies \text{P3}$, else $\text{P4}$.
   - Priority dynamically recalculated every 15 minutes by background scheduler.

2. **Automated Technician Assignment Engine (FR-22 to FR-26)**:
   - Matches required problem skill (Electrical, Mechanical, Hydraulic, etc.)
   - Filters active technicians by availability and shift
   - Assigns technician with highest skill level and fewest active tasks
   - Queues request if no technician is available; triggers preemptive re-evaluation upon task completion.

3. **Status Workflow & Audit Trail (FR-27 to FR-30, NFR-10)**:
   - `open` $\to$ `queued` $\to$ `assigned` $\to$ `in_progress` $\to$ `on_hold` $\to$ `completed` $\to$ `closed`
   - Complete status history logged with timestamp, user name, and rationale.

4. **Real-time Downtime Tracking & Financial Loss (FR-31 to FR-33)**:
   - Live timer component tracking machine downtime second-by-second
   - Estimated downtime loss calculated automatically: $\text{Loss} = \text{Downtime Hours} \times \text{Cost per Hour}$
   - Computes Mean Time To Repair (MTTR) and Mean Time Between Failures (MTBF).

5. **Preventive Maintenance Scheduling (FR-7 to FR-9, FR-34)**:
   - Calendar interval tracking with lead-day alerting
   - On-demand "Run Scheduler" trigger button and hourly automated evaluation.

6. **Recurring Failure Alerts (FR-36)**:
   - Identifies equipment with 3+ breakdowns in 30 days and provides proactive inspection recommendations.

7. **Export Reports**:
   - 1-click CSV export of downtime history and analytics.
