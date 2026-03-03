# Snowflake Cost Dashboard

A comprehensive Streamlit application for monitoring and analyzing Snowflake account usage costs across different service types with advanced projection capabilities and granular consumption analysis.

<img width="3288" height="1900" alt="image" src="https://github.com/user-attachments/assets/ef80d42d-888f-4b43-b7e5-a7ae8c955f24" />

## Updates
3/3/26 - Added Snowflake Intelligence and Cortex Agents usage tracking, Cost per Credit ($) input for accurate cost calculations

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

- **Snowflake Account**: Standard or higher edition
- **Role Requirements**: Role with access to `SNOWFLAKE.ACCOUNT_USAGE` schema (typically `ACCOUNTADMIN` or custom role with granted privileges)
- **Warehouse**: A warehouse to run the Streamlit app (XS warehouse is sufficient)

## Deployment Options

### Option 1: Deploy from Git Repository (Recommended)

This method syncs your Streamlit app with a Git repository for version control and easy updates.

#### Step 1: Clone the Repository

```bash
git clone https://github.com/your-org/snowflake-account-usage-dashboard.git
```

#### Step 2: Create a Secret for GitHub Authentication

Create a GitHub Personal Access Token (PAT) and store it as a secret in Snowflake:

```sql
USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE SECRET my_db.my_schema.github_secret
  TYPE = password
  USERNAME = 'your-github-username'
  PASSWORD = 'ghp_your_personal_access_token';
```

#### Step 3: Create an API Integration for Git

```sql
CREATE OR REPLACE API INTEGRATION github_api_integration
  API_PROVIDER = git_https_api
  API_ALLOWED_PREFIXES = ('https://github.com/your-org')
  ALLOWED_AUTHENTICATION_SECRETS = ALL
  ENABLED = TRUE;
```

#### Step 4: Create the Git Repository Object

```sql
CREATE OR REPLACE GIT REPOSITORY my_db.my_schema.cost_dashboard_repo
  API_INTEGRATION = github_api_integration
  GIT_CREDENTIALS = my_db.my_schema.github_secret
  ORIGIN = 'https://github.com/your-org/snowflake-account-usage-dashboard.git';

-- Fetch the latest from the repository
ALTER GIT REPOSITORY my_db.my_schema.cost_dashboard_repo FETCH;
```

#### Step 5: Set Up External Access Integration for PyPI

The app requires packages from PyPI. Create a network rule and external access integration:

```sql
-- Create network rule for PyPI access
CREATE OR REPLACE NETWORK RULE pypi_network_rule
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = ('pypi.org', 'pypi.python.org', 'pythonhosted.org', 'files.pythonhosted.org');

-- Create external access integration
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION pypi_access_integration
  ALLOWED_NETWORK_RULES = (pypi_network_rule)
  ENABLED = true;

-- Grant usage to your role
GRANT USAGE ON INTEGRATION pypi_access_integration TO ROLE SYSADMIN;
```

**Alternative:** Use Snowflake's managed network rule:

```sql
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION pypi_access_integration
  ALLOWED_NETWORK_RULES = (snowflake.external_access.pypi_rule)
  ENABLED = true;
```

#### Step 6: Create Streamlit App from Git Repository

**Using Snowsight UI:**

1. Sign in to Snowsight
2. Navigate to **Projects » Streamlit**
3. Click the dropdown next to **+ Streamlit** and select **Create from repository**
4. For **File location in repository**, select:
   - The repository (`cost_dashboard_repo`)
   - The branch (`main` or `dev`)
   - The file (`snowflake_cost_dashboard_V2.py`)
5. For **App location**, select the database and schema
6. For **Query warehouse** and **App warehouse**, select a warehouse (XS recommended)
7. Click **Create**


```

#### Step 7: Add External Access Integration to App

If you created the app via UI, add the PyPI integration:

1. Open your Streamlit app in Snowsight
2. Click **⋮** (more options) » **App settings**
3. Go to **External networks** tab
4. Select `pypi_access_integration`
5. Click **Save**

---

### Option 2: Manual Deployment

#### Step 1: Upload the Application File

1. Log into your Snowflake account via Snowsight
2. Navigate to **Projects -> Streamlit** in the left sidebar
3. Click **+ Streamlit App**
4. Choose:
   - **App location**: Select a database and schema (e.g., `MY_DATABASE.PUBLIC`)
   - **App warehouse**: Select an existing warehouse or create a new one (XS recommended)
5. Name your app (e.g., `COST_MONITORING_DASHBOARD`)
6. Click **Create**

#### Step 2: Deploy the Code

1. Delete the default code in the editor
2. Copy and paste the entire contents of `snowflake_cost_dashboard_V2.py`
3. In the top left `Packages` menu, select **Python Version 3.11**
4. In the same `Packages` menu, search and install: **pandas 2.3.2** , **plotly 6.3.0**
5. In the same `Packages` menu, verify the latest versions are **snowflake-snowpark-python** and **streamlit** are installed
6. Click **Run** in the top right corner
7. You may need to refresh the streamlit app to ensure the packages installed - please allow 1-2 minutes for the app to initialize. You only will need to wait this long once.

---

## Running the Application

1. Navigate to **Streamlit** in Snowsight
2. Click on your **COST_MONITORING_DASHBOARD** app
3. The app will start automatically
4. Use the sidebar to navigate between different cost analysis tabs

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

## Validation Queries

The app includes test queries in the code comments for some services. To validate data:

Example queries for AI Services are found in the app comments:
1. Open the app in edit mode
2. Find the service analyzer class (e.g., `AIServicesAnalyzer`)
3. Copy the test query from the docstring
4. Run it in a Snowflake worksheet
5. Compare results with the dashboard

Example test queries for warehouse compute and overall consumption are also provided in:
- `verify_warehouse_compute.sql`
- `verify_consumption_trends.sql`

## Troubleshooting

### No Data Displayed

**Issue**: Tabs show "No data found"

**Solutions**:
1. Verify role has access to `ACCOUNT_USAGE` schema
2. Check that your account has recent usage (data may be historical)
3. Wait up to 3 hours for data to appear in `ACCOUNT_USAGE` views
4. Click "Refresh Data" or "Clear Cache" in the sidebar

### Incorrect Storage Values

**Issue**: Storage values don't match Cost Management UI

**Solutions**:
1. The app uses `DATABASE_STORAGE_USAGE_HISTORY` which may have 1-day latency
2. Ensure the app is using the latest code (refresh the page)
3. Clear cache and reload data
4. Compare with the diagnostic queries in the code

### Performance Issues

**Issue**: App is slow or times out

**Solutions**:
1. Use a larger warehouse (S or M) for better performance
2. Reduce the date range in queries if needed
3. The app includes caching - subsequent loads will be faster

### WebSocket Connection Expires

**Issue**: App stops responding after 15 minutes of inactivity

**Solution**:
- This is expected behavior. Simply refresh the page to reconnect.
- The warehouse will auto-suspend after the WebSocket timeout to conserve credits.

### PyPI Package Download Failed

**Issue**: Error downloading packages from PyPI (DNS resolution failed)

**Solution**:
- Ensure the External Access Integration for PyPI is configured (see Step 5 in Git deployment)
- Verify the integration is attached to your Streamlit app (see Step 7)

## Billing Considerations

- The Streamlit app runs on a virtual warehouse that you select
- The warehouse remains active while the app is in use
- WebSocket connections expire after ~15 minutes of inactivity
- The warehouse will auto-suspend based on your settings
- To conserve credits: Close the app tab when not in use

## Customization

The dashboard is built with a modular design. To customize:

1. **Add New Service Tabs**: Create a new analyzer class extending `ServiceAnalyzer`
2. **Modify Queries**: Update `get_base_query()` methods in each analyzer
3. **Change Visualizations**: Modify the `render_*` methods in each analyzer
4. **Adjust Time Ranges**: Update the date filters in queries (currently 12 months)

## Support

For issues or questions:
1. Check the Troubleshooting section above
2. Verify permissions and role access
3. Review Snowflake's documentation on `ACCOUNT_USAGE` views
4. Test queries directly in a Snowflake worksheet

## Version History

- **v2.0**: Added simplified AI Services tab with accurate credit tracking
- **v1.5**: Updated Storage tab to use `DATABASE_STORAGE_USAGE_HISTORY`
- **v1.0**: Initial release with all major service analyzers

## License

Internal use only. Modify as needed for your organization.
