# Snowflake Cost Dashboard

A comprehensive Streamlit application for monitoring and analyzing Snowflake account usage costs across different service types with advanced projection capabilities and granular consumption analysis.

<img width="3288" height="1900" alt="image" src="https://github.com/user-attachments/assets/ef80d42d-888f-4b43-b7e5-a7ae8c955f24" />

## Updates

3/4/26 - Added cross-runtime compatibility (works on both Warehouse and SPCS deployments)

3/3/26 - Added Snowflake Intelligence and Cortex Agents usage tracking, SPCS and Openflow usage tracking, Cost per Credit ($) input for accurate cost calculations

10/22 - Added `snowflake_cost_dashboard_V2.py` that includes a cache layer for the AI Services tab and speeds up subsequent page loads

## Features

- **Overview**: Account-wide cost summary with yearly projections and monthly trends
- **Storage**: Database, stage, and failsafe storage analysis
- **Warehouse Compute**: Credit consumption by warehouse with trend analysis
- **Cloud Services**: Cloud services overhead monitoring and optimization insights
- **Replication**: Replication credit tracking and data transfer analysis
- **Clustering**: Automatic clustering cost analysis by table
- **Serverless**: Serverless task execution monitoring
- **AI Services**: Comprehensive AI services cost tracking (Cortex Functions, Analyst, Search, Intelligence/Agents, Fine-Tuning)
- **Client Consumption**: Usage breakdown by client application

## Prerequisites

### 1. Snowflake Account
- Standard edition or higher

### 2. Grant Account Usage Access

The dashboard queries `SNOWFLAKE.ACCOUNT_USAGE` views, which require explicit grants. Run the following as `ACCOUNTADMIN`:

```sql
USE ROLE ACCOUNTADMIN;

-- Grant IMPORTED PRIVILEGES on the SNOWFLAKE database to your role
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE SYSADMIN;
```

> **Note**: Replace `SYSADMIN` with your custom role if needed. This grant allows the role to query all views in `SNOWFLAKE.ACCOUNT_USAGE` and `SNOWFLAKE.ORGANIZATION_USAGE`.

### 3. Warehouse
- An XS warehouse is sufficient for running the app

---

## Deployment Options

### Option 1: Streamlit on Warehouse (Recommended for Simplicity)

This is the easiest deployment method. The app runs directly in Snowsight using a virtual warehouse.

#### Step 1: Create the Streamlit App

1. Log into Snowsight
2. Navigate to **Projects → Streamlit**
3. Click **+ Streamlit App**
4. Configure:
   - **App name**: `COST_MONITORING_DASHBOARD`
   - **App location**: Select your database and schema
   - **App warehouse**: Select a warehouse (XS is sufficient)
5. Click **Create**

#### Step 2: Deploy the Code

1. Delete the default code in the editor
2. Copy and paste the entire contents of `snowflake_cost_dashboard_V2.py`

#### Step 3: Install Required Packages

1. Click the **Packages** dropdown (top left of editor)
2. Search and add: **plotly**
3. Verify **streamlit** and **snowflake-snowpark-python** are already installed

#### Step 4: Run the App

1. Click **Run** in the top right corner
2. Allow 1-2 minutes for initial package installation
3. The app will load automatically

---

### Option 2: Streamlit on SPCS (Snowpark Container Services)

This deployment runs the app in a container, which provides more flexibility for custom packages and long-running sessions.

#### Step 1: Create Required Objects

Run the following SQL as a role with appropriate privileges:

```sql
USE ROLE ACCOUNTADMIN;

-- Create a compute pool (if you don't have one)
CREATE COMPUTE POOL IF NOT EXISTS STREAMLIT_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_S
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 300;

-- Grant usage to your role
GRANT USAGE ON COMPUTE POOL STREAMLIT_POOL TO ROLE SYSADMIN;
```
> **Note**: Replace `SYSADMIN` with your custom role if needed. This grant allows the role to query all views in `SNOWFLAKE.ACCOUNT_USAGE` and `SNOWFLAKE.ORGANIZATION_USAGE`.

#### Step 2: Create External Access Integration for PyPI

The SPCS app needs to download packages from PyPI:

```sql
USE ROLE ACCOUNTADMIN;

-- Create network rule for PyPI access
CREATE OR REPLACE NETWORK RULE pypi_network_rule
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = ('pypi.org', 'pypi.python.org', 'pythonhosted.org', 'files.pythonhosted.org');

-- Create external access integration
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION pypi_access_integration
  ALLOWED_NETWORK_RULES = (pypi_network_rule)
  ENABLED = TRUE;

-- Grant usage to your role (SYSADMIN or custom role)
GRANT USAGE ON INTEGRATION pypi_access_integration TO ROLE SYSADMIN;
```
> **Note**: Replace `SYSADMIN` with your custom role if needed. This grant allows the role to query all views in `SNOWFLAKE.ACCOUNT_USAGE` and `SNOWFLAKE.ORGANIZATION_USAGE`.

**Alternative** - Use Snowflake's managed PyPI rule:

```sql
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION pypi_access_integration
  ALLOWED_NETWORK_RULES = (snowflake.external_access.pypi_rule)
  ENABLED = TRUE;

GRANT USAGE ON INTEGRATION pypi_access_integration TO ROLE SYSADMIN;
```
> **Note**: Replace `SYSADMIN` with your custom role if needed. This grant allows the role to query all views in `SNOWFLAKE.ACCOUNT_USAGE` and `SNOWFLAKE.ORGANIZATION_USAGE`.


#### Step 4: Create the Streamlit App on SPCS

**Using Snowsight UI:**
1. Navigate to **Projects → Streamlit**
2. Click **+ Streamlit App**
3. Select **Snowpark Container Services** as the runtime
4. Configure:
   - **App name**: `COST_MONITORING_DASHBOARD_SPCS`
   - **App location**: Select database and schema to save
   - **Compute pool**: Select your compute pool
   - **Query warehouse**: Select your query warehouse
5. Click **Create**


#### Step 5: Enable PyPI Integration in App Settings

1. Open your Streamlit app in Snowsight
2. Click the **⋮** (three dots menu) → **App settings**
3. Go to the **External access** tab
4. Toggle ON the `pypi_access_integration`
5. Click **Save**


#### Step 6: Edit code files

1. Open your Streamlit app in Snowsight
2. In the file explorer on the left, copy and paste the entire contents of `snowflake_cost_dashboard_V2.py`
3. In the file explorer on the left, copy and paste the entire contents of `pyproject.toml`
4. Click **Run**
5. Note: you may need to reboot the app in the top right settings.

---

## Running the Application

1. Navigate to **Projects → Streamlit** in Snowsight
2. Click on your app (**COST_MONITORING_DASHBOARD** or **COST_MONITORING_DASHBOARD_SPCS**)
3. The app will start automatically
4. Use the sidebar to navigate between different cost analysis tabs
5. Enter your **Cost per Credit ($)** in the sidebar for accurate cost calculations

## Data Latency

Note that `ACCOUNT_USAGE` views have latency:
- Most views: Up to 3 hours
- Storage views: Daily snapshots
- The app displays data as of the most recent available date

## Features Overview

### Overview Tab
- **Yearly Projection**: Estimates annual costs based on current usage
- **Monthly Trends**: Historical cost trends by service type
- **Service Breakdown**: Visual breakdown of costs across all services

### Storage Tab
- Database storage by date
- Stage and failsafe storage tracking
- Storage growth trends
- Credit estimates

### Warehouse Compute Tab
- Credit consumption by warehouse
- Compute vs. cloud services breakdown
- Daily consumption trends
- Warehouse efficiency metrics

### AI Services Tab
- **Account-Level**: Overall AI services credit consumption
- **Cortex Functions**: Function and model-level usage
- **Cortex Analyst**: User-based analytics
- **Cortex Search**: Search service consumption
- **Snowflake Intelligence/Agents**: Agent and tool usage tracking
- **Fine-Tuning**: Model fine-tuning costs

Each section includes:
- Summary metrics
- Trend charts
- Detailed data tables with test queries in code comments

## Troubleshooting

### "No data found" or Empty Tables

1. **Verify account usage access**: Run this query to test:
   ```sql
   SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY LIMIT 10;
   ```
2. If you get a permission error, ensure the `IMPORTED PRIVILEGES` grant was applied (see Prerequisites)
3. Data may take up to 3 hours to appear in `ACCOUNT_USAGE` views

### Charts Show Incorrect Data

1. Ensure you're running the latest version of `snowflake_cost_dashboard_V2.py`
2. For Warehouse deployments: Re-create the app or re-paste the code
3. For SPCS deployments: Re-upload the file to the stage and restart the app

### PyPI Package Download Failed

**Error**: DNS resolution failed or package download error

**Solution**:
1. Verify the External Access Integration exists and is enabled
2. For SPCS: Check that `pypi_access_integration` is attached to the app (App Settings → External access)
3. For Warehouse: This shouldn't occur as packages come from Anaconda

### Performance Issues

1. Use a larger warehouse (S or M) for faster query execution
2. The app includes caching - subsequent loads are faster
3. Reduce date ranges if queries timeout

### WebSocket Connection Expires

This is expected behavior after ~15 minutes of inactivity. Refresh the page to reconnect.

## Billing Considerations

- **Warehouse Runtime**: The warehouse remains active while the app is in use and auto-suspends based on your settings
- **SPCS Runtime**: The compute pool remains active and auto-suspends after the configured timeout
- **Tip**: Close the app tab when not in use to conserve credits

## Customization

The dashboard uses a modular design:

1. **Add New Service Tabs**: Create a new analyzer class extending `ServiceAnalyzer`
2. **Modify Queries**: Update `get_base_query()` methods in each analyzer
3. **Change Visualizations**: Modify the `render_*` methods in each analyzer
4. **Adjust Time Ranges**: Update the date filters in queries (currently 12 months)

## Version History

- **v2.1**: Cross-runtime compatibility (Warehouse + SPCS), improved chart rendering
- **v2.0**: Added simplified AI Services tab with accurate credit tracking
- **v1.5**: Updated Storage tab to use `DATABASE_STORAGE_USAGE_HISTORY`
- **v1.0**: Initial release with all major service analyzers

## License

Internal use only. Modify as needed for your organization.
