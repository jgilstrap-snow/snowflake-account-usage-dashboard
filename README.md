# Snowflake Account Usage Dashboard

A comprehensive Streamlit application for monitoring and analyzing Snowflake account usage costs across different service types with advanced projection capabilities and granular consumption analysis.

## Features

- **📊 Overview**: Account-wide cost summary with yearly projections and monthly trends
- **💾 Storage**: Database, stage, and failsafe storage analysis
- **💻 Warehouse Compute**: Credit consumption by warehouse with trend analysis
- **☁️ Cloud Services**: Cloud services overhead monitoring and optimization insights
- **🔄 Replication**: Replication credit tracking and data transfer analysis
- **🔧 Clustering**: Automatic clustering cost analysis by table
- **⚡ Serverless**: Serverless task execution monitoring
- **🤖 AI Services**: Comprehensive AI services cost tracking (Cortex Functions, Analyst, Search, Document AI, Fine-Tuning)
- **📱 Client Consumption**: Usage breakdown by client application

## Prerequisites

- **Snowflake Account**: Standard or higher edition
- **Role Requirements**: Role with access to `SNOWFLAKE.ACCOUNT_USAGE` schema (typically `ACCOUNTADMIN` or custom role with granted privileges)
- **Warehouse**: A warehouse to run the Streamlit app (XS warehouse is sufficient)

## Installation

### Step 1: Upload the Application File

1. Log into your Snowflake account via Snowsight
2. Navigate to **Streamlit** in the left sidebar
3. Click **+ Streamlit App**
4. Choose:
   - **App location**: Select a database and schema (e.g., `MY_DATABASE.PUBLIC`)
   - **App warehouse**: Select an existing warehouse or create a new one (XS recommended)
5. Name your app (e.g., `COST_MONITORING_DASHBOARD`)
6. Click **Create**

### Step 2: Deploy the Code

1. Delete the default code in the editor
2. Copy and paste the entire contents of `snowflake_cost_dashboard.py`
3. Click **Run** in the top right corner

### Step 3: Grant Required Permissions

The app needs access to `ACCOUNT_USAGE` views. Run these commands as `ACCOUNTADMIN`:

```sql
-- Grant usage on ACCOUNT_USAGE schema to your role
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <YOUR_ROLE>;

-- Or create a custom role with specific grants
CREATE ROLE IF NOT EXISTS COST_MONITORING_ROLE;

GRANT USAGE ON DATABASE SNOWFLAKE TO ROLE COST_MONITORING_ROLE;
GRANT USAGE ON SCHEMA SNOWFLAKE.ACCOUNT_USAGE TO ROLE COST_MONITORING_ROLE;

-- Grant access to specific views
GRANT SELECT ON VIEW SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY TO ROLE COST_MONITORING_ROLE;
GRANT SELECT ON VIEW SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY TO ROLE COST_MONITORING_ROLE;
GRANT SELECT ON VIEW SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY TO ROLE COST_MONITORING_ROLE;
GRANT SELECT ON VIEW SNOWFLAKE.ACCOUNT_USAGE.STAGE_STORAGE_USAGE_HISTORY TO ROLE COST_MONITORING_ROLE;
GRANT SELECT ON VIEW SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY TO ROLE COST_MONITORING_ROLE;
-- Add other views as needed

-- Grant role to users
GRANT ROLE COST_MONITORING_ROLE TO USER <USERNAME>;
```

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
- **Document AI**: Document processing metrics
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
