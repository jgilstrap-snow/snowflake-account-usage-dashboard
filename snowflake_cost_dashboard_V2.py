"""
Snowflake Streamlit Cost Monitoring Dashboard - OPTIMIZED VERSION

A comprehensive application for monitoring and analyzing Snowflake account usage costs
across different service types with advanced projection capabilities and granular 
consumption analysis.

PERFORMANCE OPTIMIZATIONS:
- AI Services queries now use cached results (1-hour TTL) for faster load times
- All 6 AI service data fetches (Account-Level, Cortex Functions, Cortex Analyst, 
  Cortex Search, Document AI, Fine-Tuning) leverage @st.cache_data decorator
- Expected improvement: 45s initial load → ~3-5s on subsequent loads within cache window
- No functionality changes - all features preserved
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union, Tuple
import time
from dataclasses import dataclass
from enum import Enum

# Configure Plotly to use SVG renderer instead of WebGL for better compatibility
import plotly.io as pio
pio.renderers.default = "svg"

# Configure Plotly to avoid WebGL issues
px.defaults.template = "plotly_white"
px.defaults.width = None
px.defaults.height = None

# Set global config to disable WebGL for all charts
import plotly.graph_objects as go
go.Figure.show = lambda self, *args, **kwargs: self.show(*args, renderer="svg", **kwargs)

# Helper function to render Plotly charts without WebGL
def render_plotly_chart(fig, use_container_width=True, **kwargs):
    """Render Plotly chart with WebGL disabled."""
    config = {
        'displayModeBar': False,
        'toImageButtonOptions': {'format': 'svg'},
        'staticPlot': False,
        'responsive': True
    }
    config.update(kwargs.get('config', {}))
    return st.plotly_chart(fig, use_container_width=use_container_width, config=config)


def format_credits_with_dollars(credits: float, credit_price: float = None) -> str:
    """
    Format credits with dollar amount for display.
    
    Args:
        credits: Credit value to format
        credit_price: Price per credit (uses session state if not provided)
        
    Returns:
        Formatted string like "1,234 ($2,468)"
    """
    if pd.isna(credits) or credits is None:
        return "0 ($0.00)"
    
    if credit_price is None:
        credit_price = st.session_state.get('credit_price', 2.00)
    
    dollar_amount = credits * credit_price
    
    if credits < 0.01:
        credit_str = f"{credits:.4f}"
    elif credits < 1:
        credit_str = f"{credits:.3f}"
    elif credits < 100:
        credit_str = f"{credits:.2f}"
    else:
        credit_str = f"{credits:,.0f}"
    
    if dollar_amount < 1:
        dollar_str = f"${dollar_amount:.2f}"
    elif dollar_amount < 1000:
        dollar_str = f"${dollar_amount:,.2f}"
    else:
        dollar_str = f"${dollar_amount:,.0f}"
    
    return f"{credit_str} ({dollar_str})"


def get_time_range_string(data: pd.DataFrame, date_column: str) -> str:
    """
    Generate a time range string for chart axis labels based on data date range.
    
    Args:
        data (pd.DataFrame): DataFrame containing date data
        date_column (str): Name of the date column
        
    Returns:
        str: Formatted time range string (e.g., "Jan 2024 - Dec 2024")
    """
    if data.empty or date_column not in data.columns:
        return ""
    
    try:
        date_series = pd.to_datetime(data[date_column])
        
        min_date = date_series.min()
        max_date = date_series.max()
        
        if pd.isna(min_date) or pd.isna(max_date):
            return ""
        
        if min_date.year == max_date.year:
            if min_date.month == max_date.month:
                return min_date.strftime("%b %Y")
            else:
                return f"{min_date.strftime('%b')} - {max_date.strftime('%b %Y')}"
        else:
            return f"{min_date.strftime('%b %Y')} - {max_date.strftime('%b %Y')}"
    except Exception:
        return ""


def update_chart_with_time_range(fig, data: pd.DataFrame, date_column: str, 
                                x_axis_label: str = "Date", chart_title: str = None):
    """
    Update a Plotly chart to include time range information in axis titles and chart subtitle.
    
    Args:
        fig: Plotly figure object
        data (pd.DataFrame): DataFrame containing the chart data
        date_column (str): Name of the date column in the data
        x_axis_label (str): Base label for x-axis (e.g., "Month", "Date")
        chart_title (str): Optional chart title to update with time range
    """
    time_range = get_time_range_string(data, date_column)
    
    if time_range:
        # Update x-axis title to include time range
        x_title_with_range = f"{x_axis_label} ({time_range})"
        fig.update_xaxes(title_text=x_title_with_range)
        
        # If chart title provided, add time range as subtitle
        if chart_title:
            title_with_range = f"{chart_title}<br><sub>Data Range: {time_range}</sub>"
            fig.update_layout(title=title_with_range)

# Import for Snowflake Streamlit environment
SNOWFLAKE_AVAILABLE = False
get_active_session = None

try:
    from snowflake.snowpark.context import get_active_session
    from snowflake.snowpark import Session
    SNOWFLAKE_AVAILABLE = True
except ImportError:
    pass

try:
    import snowflake.connector
    from snowflake.connector import DictCursor
except ImportError:
    pass


class ViewType(Enum):
    """Enumeration for different view types in service analysis."""
    WAREHOUSE = "warehouse"
    USER = "user"
    CLIENT = "client"


@dataclass
class ServiceUsageData:
    """Data structure for service usage information."""
    service_type: str
    usage_date: datetime
    credits_used: float
    warehouse_name: Optional[str] = None
    user_name: Optional[str] = None
    client_application_name: Optional[str] = None
    mom_change: Optional[float] = None


@dataclass
class ProjectionData:
    """Data structure for yearly projection information."""
    actual_ytd: float
    projected_total: float
    run_rate_period: int
    daily_average: float
    remaining_days: int
    projection_date: datetime


# Data Processing Utilities
class DataProcessor:
    """
    Utility class for data processing operations used by service analyzers.
    Provides aggregation, transformation, and validation functions.
    """
    
    @staticmethod
    def aggregate_monthly_consumption(data: pd.DataFrame, grouping_column: str, 
                                    credit_column: str = 'CREDITS_USED',
                                    date_column: str = 'START_TIME') -> pd.DataFrame:
        """
        Aggregate consumption data by month and grouping column.
        
        Args:
            data (pd.DataFrame): Raw consumption data
            grouping_column (str): Column to group by (warehouse, user, client)
            credit_column (str): Column containing credit usage
            date_column (str): Column containing timestamp data
            
        Returns:
            pd.DataFrame: Aggregated monthly data with MoM calculations
        """
        if data.empty:
            return pd.DataFrame()
        
        try:
            # Ensure date column is datetime
            if date_column in data.columns:
                data[date_column] = pd.to_datetime(data[date_column])
            
            # Create monthly aggregation
            monthly_data = data.groupby([
                pd.Grouper(key=date_column, freq='M'),
                grouping_column
            ]).agg({
                credit_column: 'sum',
                'COMPUTE_CREDITS': 'sum' if 'COMPUTE_CREDITS' in data.columns else lambda x: 0,
                'CLOUD_SERVICES_CREDITS': 'sum' if 'CLOUD_SERVICES_CREDITS' in data.columns else lambda x: 0
            }).reset_index()
            
            # Rename columns for consistency
            monthly_data.rename(columns={
                date_column: 'USAGE_MONTH',
                credit_column: 'TOTAL_CREDITS',
                grouping_column: 'GROUP_BY'
            }, inplace=True)
            
            # Calculate month-over-month changes
            monthly_data = DataProcessor.calculate_mom_changes(monthly_data)
            
            return monthly_data
            
        except Exception as e:
            st.error(f"Error in monthly aggregation: {str(e)}")
            return pd.DataFrame()
    
    @staticmethod
    def calculate_mom_changes(data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate month-over-month percentage changes for aggregated data.
        
        Args:
            data (pd.DataFrame): Monthly aggregated data
            
        Returns:
            pd.DataFrame: Data with MoM change calculations
        """
        if data.empty or 'USAGE_MONTH' not in data.columns:
            return data
        
        try:
            # Sort by usage month and group
            data = data.sort_values(['GROUP_BY', 'USAGE_MONTH'])
            
            # Calculate MoM changes within each group
            data['PREV_MONTH_CREDITS'] = data.groupby('GROUP_BY')['TOTAL_CREDITS'].shift(1)
            
            # Calculate percentage change
            data['MOM_PERCENT_CHANGE'] = data.apply(
                lambda row: ((row['TOTAL_CREDITS'] - row['PREV_MONTH_CREDITS']) / row['PREV_MONTH_CREDITS'] * 100)
                if pd.notna(row['PREV_MONTH_CREDITS']) and row['PREV_MONTH_CREDITS'] > 0
                else None,
                axis=1
            )
            
            return data
            
        except Exception as e:
            st.error(f"Error calculating MoM changes: {str(e)}")
            return data
    
    @staticmethod
    def transform_for_view_type(data: pd.DataFrame, view_type: ViewType, 
                              service_name: str) -> pd.DataFrame:
        """
        Transform raw data for specific view type requirements.
        
        Args:
            data (pd.DataFrame): Raw service data
            view_type (ViewType): Target view type
            service_name (str): Service being analyzed
            
        Returns:
            pd.DataFrame: Transformed data ready for analysis
        """
        if data.empty:
            return pd.DataFrame()
        
        try:
            # Get appropriate grouping column based on view type
            grouping_mapping = {
                ViewType.WAREHOUSE: 'WAREHOUSE_NAME',
                ViewType.USER: 'USER_NAME',
                ViewType.CLIENT: 'CLIENT_APPLICATION_NAME'
            }
            
            target_column = grouping_mapping.get(view_type)
            if not target_column or target_column not in data.columns:
                # Handle missing columns gracefully
                if view_type == ViewType.CLIENT and 'CLIENT_APPLICATION_NAME' not in data.columns:
                    # For client view, try to get from query history if not available
                    data['CLIENT_APPLICATION_NAME'] = 'Unknown Client'
                elif view_type == ViewType.USER and 'USER_NAME' not in data.columns:
                    data['USER_NAME'] = 'Unknown User'
                elif view_type == ViewType.WAREHOUSE and 'WAREHOUSE_NAME' not in data.columns:
                    data['WAREHOUSE_NAME'] = 'Unknown Warehouse'
            
            # Filter out null values in grouping column
            if target_column in data.columns:
                data = data[data[target_column].notna()]
                data = data[data[target_column] != '']
            
            # Add service-specific transformations
            if service_name.lower() == 'storage':
                # For storage, ensure we have storage-specific metrics
                if 'STORAGE_BYTES' in data.columns:
                    data['STORAGE_GB'] = data['STORAGE_BYTES'] / (1024**3)
            
            elif service_name.lower() == 'compute':
                # For compute, ensure compute-specific metrics
                if 'EXECUTION_TIME' in data.columns:
                    data['EXECUTION_HOURS'] = data['EXECUTION_TIME'] / 3600
            
            return data
            
        except Exception as e:
            st.error(f"Error transforming data for {view_type.value} view: {str(e)}")
            return data
    
    @staticmethod
    def validate_data_consistency(data: pd.DataFrame, required_columns: List[str]) -> Tuple[bool, List[str]]:
        """
        Validate data consistency and required columns.
        
        Args:
            data (pd.DataFrame): Data to validate
            required_columns (List[str]): List of required column names
            
        Returns:
            Tuple[bool, List[str]]: (is_valid, list_of_missing_columns)
        """
        if data.empty:
            return False, ["Data is empty"]
        
        missing_columns = []
        for col in required_columns:
            if col not in data.columns:
                missing_columns.append(col)
        
        # Check for data quality issues
        quality_issues = []
        
        # Check for all-null columns
        for col in data.columns:
            if data[col].isna().all():
                quality_issues.append(f"Column '{col}' contains only null values")
        
        # Check for reasonable credit values
        if 'TOTAL_CREDITS' in data.columns:
            negative_credits = (data['TOTAL_CREDITS'] < 0).sum()
            if negative_credits > 0:
                quality_issues.append(f"{negative_credits} rows have negative credit values")
        
        # Check date ranges
        if 'USAGE_MONTH' in data.columns:
            date_range_days = (data['USAGE_MONTH'].max() - data['USAGE_MONTH'].min()).days
            if date_range_days > 400:  # More than ~13 months
                quality_issues.append("Data spans more than expected range (>13 months)")
        
        is_valid = len(missing_columns) == 0 and len(quality_issues) == 0
        all_issues = missing_columns + quality_issues
        
        return is_valid, all_issues
    
    @staticmethod
    def handle_empty_result_set(service_name: str, view_type: ViewType, 
                              query_type: str = "data") -> None:
        """
        Handle empty result sets with appropriate user messaging.
        
        Args:
            service_name (str): Name of the service
            view_type (ViewType): Current view type
            query_type (str): Type of query that returned empty results
        """
        view_name = view_type.value.replace('_', ' ').title()
        
        st.warning(f"No {service_name.lower()} {query_type} found for {view_name} view")
        
        with st.expander("**Possible Reasons & Solutions**"):
            st.markdown(f"""
            **Why might {service_name} data be empty?**
            
            • **Time Range**: Data might be outside the current query time range
            • **Service Usage**: {service_name} services may not have been used recently
            • **View Permissions**: Account may lack access to specific {service_name.upper()} views
            • **Data Latency**: Account usage data has up to 3-hour delay
            
            **Troubleshooting Steps:**
            1. Check if {service_name.lower()} services are actively used in your account
            2. Verify your current role has ACCOUNT_USAGE schema access
            3. Try switching to a different view type ({', '.join([vt.value for vt in ViewType if vt != view_type])})
            4. Wait for data to propagate (up to 3 hours for recent usage)
            
            **Data Sources for {service_name}:**
            """)
            
            # Add service-specific data source info
            if service_name.lower() == 'storage':
                st.code("• SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE")
            elif service_name.lower() == 'compute':
                st.code("• SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY")
            elif service_name.lower() == 'cloud services':
                st.code("• SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY (SERVICE_TYPE filtering)")
            else:
                st.code(f"• SNOWFLAKE.ACCOUNT_USAGE.{service_name.upper()}_HISTORY")
    
    @staticmethod
    def format_credits_display(credits: float, include_dollars: bool = True) -> str:
        """
        Format credit values for consistent display with optional dollar amount.
        
        Args:
            credits (float): Credit value to format
            include_dollars (bool): Whether to include dollar conversion
            
        Returns:
            str: Formatted credit string with optional dollar amount
        """
        if pd.isna(credits):
            return "N/A"
        
        if credits == 0:
            credit_str = "0"
        elif credits < 0.01:
            credit_str = f"{credits:.4f}"
        elif credits < 1:
            credit_str = f"{credits:.3f}"
        elif credits < 100:
            credit_str = f"{credits:.2f}"
        else:
            credit_str = f"{credits:,.0f}"
        
        if include_dollars:
            credit_price = st.session_state.get('credit_price', 2.00)
            dollar_amount = credits * credit_price
            if dollar_amount < 1:
                dollar_str = f"${dollar_amount:.2f}"
            elif dollar_amount < 1000:
                dollar_str = f"${dollar_amount:,.2f}"
            else:
                dollar_str = f"${dollar_amount:,.0f}"
            return f"{credit_str} ({dollar_str})"
        
        return credit_str
    
    @staticmethod
    def get_date_range_filter(data: pd.DataFrame, date_column: str = 'USAGE_MONTH') -> Tuple[pd.Timestamp, pd.Timestamp]:
        """
        Get appropriate date range for filtering based on data availability.
        
        Args:
            data (pd.DataFrame): Data to analyze
            date_column (str): Date column name
            
        Returns:
            Tuple[pd.Timestamp, pd.Timestamp]: (min_date, max_date)
        """
        if data.empty or date_column not in data.columns:
            # Default to last 12 months
            end_date = pd.Timestamp.now()
            start_date = end_date - pd.DateOffset(months=12)
            return start_date, end_date
        
        try:
            min_date = data[date_column].min()
            max_date = data[date_column].max()
            
            # Ensure we have reasonable bounds
            if pd.isna(min_date) or pd.isna(max_date):
                end_date = pd.Timestamp.now()
                start_date = end_date - pd.DateOffset(months=12)
                return start_date, end_date
            
            return min_date, max_date
            
        except Exception:
            # Fallback to default range
            end_date = pd.Timestamp.now()
            start_date = end_date - pd.DateOffset(months=12)
            return start_date, end_date


class ServiceAnalyzer(ABC):
    """
    Abstract base class for all service-specific analyzers.
    Provides common functionality for three-way toggle support and data visualization.
    """
    
    def __init__(self, service_name: str, data_manager, cache_ttl: int = 3600):
        """
        Initialize the service analyzer.
        
        Args:
            service_name (str): Name of the service being analyzed
            data_manager: DataAccessManager instance for database operations
            cache_ttl (int): Cache time-to-live in seconds
        """
        self.service_name = service_name
        self.data_manager = data_manager
        self.cache_ttl = cache_ttl
        
        # View configuration
        self.view_types = {
            ViewType.WAREHOUSE: {
                'name': 'Warehouse',
                'column': 'WAREHOUSE_NAME',
                'description': 'Group by warehouse for resource-based analysis'
            },
            ViewType.USER: {
                'name': 'User',
                'column': 'USER_NAME', 
                'description': 'Group by user for person-based analysis'
            },
            ViewType.CLIENT: {
                'name': 'Client Connection',
                'column': 'CLIENT_APPLICATION_NAME',
                'description': 'Group by client application for tool-based analysis'
            }
        }
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering the service analysis.
        Orchestrates connection validation, data loading, and UI rendering.
        """
        st.markdown(f"### {self.service_name} Analysis")
        st.markdown(f"Comprehensive {self.service_name.lower()} usage analysis with multiple view options.")
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            return
        
        # Render view toggle controls
        selected_view = self.render_view_toggle()
        
        # Get and display data based on selected view
        service_data = self.get_service_data(selected_view)
        
        # Handle empty result sets with appropriate messaging
        if service_data is None or service_data.empty:
            DataProcessor.handle_empty_result_set(self.service_name, selected_view)
            return
        
        # Validate data consistency
        required_columns = ['USAGE_MONTH', 'TOTAL_CREDITS', self.get_grouping_column(selected_view)]
        is_valid, issues = DataProcessor.validate_data_consistency(service_data, required_columns)
        
        if not is_valid:
            with st.expander("**Data Quality Issues Detected**"):
                for issue in issues:
                    st.warning(f"• {issue}")
            
            # Continue with available data but show warning
            if not service_data.empty:
                st.info("Proceeding with available data despite quality issues")
            else:
                return
        
        # Transform data for the selected view type
        transformed_data = DataProcessor.transform_for_view_type(service_data, selected_view, self.service_name)
        
        # Render analysis components
        self.render_summary_metrics(transformed_data, selected_view)
        self.render_analysis_tabs(transformed_data, selected_view)
    
    def render_view_toggle(self) -> ViewType:
        """
        Render the three-way toggle for warehouse/user/client views.
        
        Returns:
            ViewType: Selected view type
        """
        st.markdown("#### 🎛Analysis View")
        
        # Create columns for the toggle
        col1, col2 = st.columns([2, 1])
        
        with col1:
            view_options = [view_type.value for view_type in ViewType]
            view_labels = [self.view_types[view_type]['name'] for view_type in ViewType]
            
            selected_index = st.radio(
                "Select analysis view:",
                range(len(view_options)),
                format_func=lambda x: view_labels[x],
                key=f"{self.service_name.lower()}_view_toggle",
                horizontal=True
            )
            
            selected_view = ViewType(view_options[selected_index])
        
        with col2:
            # Show description of selected view
            st.caption(f"**{self.view_types[selected_view]['name']} View**")
            st.caption(self.view_types[selected_view]['description'])
        
        return selected_view
    
    def render_summary_metrics(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Render summary metrics for the service data using DataProcessor utilities.
        
        Args:
            data (pd.DataFrame): Service usage data
            view_type (ViewType): Current view type
        """
        if data.empty:
            return
        
        # Calculate summary metrics using DataProcessor utilities
        total_credits = data['TOTAL_CREDITS'].sum()
        avg_monthly_credits = data.groupby('USAGE_MONTH')['TOTAL_CREDITS'].sum().mean()
        unique_entities = data[self.get_grouping_column(view_type)].nunique()
        
        # Get current and previous month for MoM calculation
        current_month = data['USAGE_MONTH'].max()
        current_month_data = data[data['USAGE_MONTH'] == current_month]
        current_total = current_month_data['TOTAL_CREDITS'].sum()
        
        # Calculate MoM change using data processor
        previous_months = data[data['USAGE_MONTH'] < current_month]['USAGE_MONTH'].unique()
        mom_change = 0
        if len(previous_months) > 0:
            prev_month = max(previous_months)
            prev_month_data = data[data['USAGE_MONTH'] == prev_month]
            prev_total = prev_month_data['TOTAL_CREDITS'].sum()
            if prev_total > 0:
                mom_change = ((current_total - prev_total) / prev_total) * 100
        
        # Display metrics with formatted values
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label=f"Total {self.service_name} Credits",
                value=DataProcessor.format_credits_display(total_credits),
                help=f"Total credits used across all time periods"
            )
        
        with col2:
            st.metric(
                label=f"Current Month",
                value=DataProcessor.format_credits_display(current_total),
                delta=f"{mom_change:+.1f}%" if mom_change != 0 else None,
                help=f"Credits used in {current_month.strftime('%Y-%m')}"
            )
        
        with col3:
            st.metric(
                label=f"Monthly Average",
                value=DataProcessor.format_credits_display(avg_monthly_credits),
                help=f"Average monthly credits across all periods"
            )
        
        with col4:
            view_name = self.view_types[view_type]['name']
            st.metric(
                label=f"Active {view_name}s",
                value=f"{unique_entities}",
                help=f"Number of unique {view_name.lower()}s with {self.service_name.lower()} usage"
            )
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Render analysis tabs with different visualizations.
        
        Args:
            data (pd.DataFrame): Service usage data
            view_type (ViewType): Current view type
        """
        # Create tabs for different analysis views
        tab1, tab2, tab3 = st.tabs(["Trends", "Breakdown", "Detailed Data"])
        
        with tab1:
            self.render_trends_chart(data, view_type)
        
        with tab2:
            self.render_breakdown_charts(data, view_type)
        
        with tab3:
            self.render_detailed_table(data, view_type)
    
    def render_trends_chart(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Render trends chart for the service data.
        
        Args:
            data (pd.DataFrame): Service usage data
            view_type (ViewType): Current view type
        """
        grouping_col = self.get_grouping_column(view_type)
        view_name = self.view_types[view_type]['name']
        
        # Group data for visualization
        trend_data = data.groupby(['USAGE_MONTH', grouping_col])['TOTAL_CREDITS'].sum().reset_index()
        
        if trend_data.empty:
            st.warning("No trend data available")
            return
        
        # Create line chart
        fig = px.line(
            trend_data,
            x='USAGE_MONTH',
            y='TOTAL_CREDITS',
            color=grouping_col,
            title=f'{self.service_name} Credits Usage Trends by {view_name}',
            labels={
                'USAGE_MONTH': 'Month',
                'TOTAL_CREDITS': 'Credits Used',
                grouping_col: view_name
            }
        )
        
        fig.update_layout(
            height=500,
            hovermode='x unified',
            legend=dict(
                orientation="v",
                yanchor="top",
                y=1,
                xanchor="left",
                x=1.02
            )
        )
        
        fig.update_traces(line=dict(width=3))
        render_plotly_chart(fig)
    
    def render_breakdown_charts(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Render breakdown charts for current month analysis.
        
        Args:
            data (pd.DataFrame): Service usage data
            view_type (ViewType): Current view type
        """
        grouping_col = self.get_grouping_column(view_type)
        view_name = self.view_types[view_type]['name']
        
        # Get current month data
        current_month = data['USAGE_MONTH'].max()
        current_data = data[data['USAGE_MONTH'] == current_month].copy()
        
        if current_data.empty:
            st.warning("No current month data available")
            return
        
        # Aggregate by grouping column
        breakdown_data = current_data.groupby(grouping_col)['TOTAL_CREDITS'].sum().reset_index()
        breakdown_data = breakdown_data.sort_values('TOTAL_CREDITS', ascending=False)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Pie chart
            fig_pie = px.pie(
                breakdown_data.head(10),  # Top 10 to avoid overcrowding
                values='TOTAL_CREDITS',
                names=grouping_col,
                title=f'{self.service_name} Distribution by {view_name} (Top 10)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        with col2:
            # Horizontal bar chart
            fig_bar = px.bar(
                breakdown_data.head(15),  # Top 15 for better readability
                x='TOTAL_CREDITS',
                y=grouping_col,
                orientation='h',
                title=f'{self.service_name} Credits by {view_name} (Top 15)',
                labels={'TOTAL_CREDITS': 'Credits Used', grouping_col: view_name}
            )
            fig_bar.update_layout(height=600)
            render_plotly_chart(fig_bar)
    
    def render_detailed_table(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Render detailed data table with filtering options.
        
        Args:
            data (pd.DataFrame): Service usage data
            view_type (ViewType): Current view type
        """
        grouping_col = self.get_grouping_column(view_type)
        view_name = self.view_types[view_type]['name']
        
        st.markdown(f"#### Detailed {self.service_name} Data by {view_name}")
        
        # Filtering options
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Entity filter
            all_entities = ['All'] + sorted(data[grouping_col].dropna().unique().tolist())
            selected_entity = st.selectbox(
                f"Filter by {view_name}:",
                all_entities,
                key=f"{self.service_name}_{view_type.value}_entity_filter"
            )
        
        with col2:
            # Month range filter
            min_month = data['USAGE_MONTH'].min().to_pydatetime()
            max_month = data['USAGE_MONTH'].max().to_pydatetime()
            if min_month < max_month:
                selected_months = st.slider(
                    "Select Month Range:",
                    value=(min_month, max_month),
                    min_value=min_month,
                    max_value=max_month,
                    format="YYYY-MM",
                    key=f"{self.service_name}_{view_type.value}_month_filter"
                )
            else:
                st.info(f"Data: {min_month.strftime('%Y-%m')}")
                selected_months = (min_month, max_month)
        
        with col3:
            # Credits threshold
            min_credits = st.number_input(
                "Minimum Credits:",
                min_value=0.0,
                value=0.0,
                step=100.0,
                key=f"{self.service_name}_{view_type.value}_credits_filter"
            )
        
        # Apply filters
        filtered_data = data.copy()
        
        if selected_entity != 'All':
            filtered_data = filtered_data[filtered_data[grouping_col] == selected_entity]
        
        # Convert selected months for comparison
        start_month = pd.Timestamp(selected_months[0])
        end_month = pd.Timestamp(selected_months[1])
        filtered_data = filtered_data[
            (filtered_data['USAGE_MONTH'] >= start_month) &
            (filtered_data['USAGE_MONTH'] <= end_month)
        ]
        
        filtered_data = filtered_data[filtered_data['TOTAL_CREDITS'] >= min_credits]
        
        if filtered_data.empty:
            st.warning("No data matches the selected filters")
            return
        
        # Prepare display data
        display_data = filtered_data.copy()
        display_data['USAGE_MONTH'] = display_data['USAGE_MONTH'].dt.strftime('%Y-%m')
        display_columns = ['USAGE_MONTH', grouping_col, 'TOTAL_CREDITS']
        
        # Add additional columns if available
        if 'MOM_PERCENT_CHANGE' in display_data.columns:
            display_columns.append('MOM_PERCENT_CHANGE')
        
        # Display table
        st.dataframe(
            display_data[display_columns].sort_values(['USAGE_MONTH', 'TOTAL_CREDITS'], ascending=[False, False]),
            column_config={
                'USAGE_MONTH': 'Month',
                grouping_col: view_name,
                'TOTAL_CREDITS': st.column_config.NumberColumn('Total Credits', format="%.2f"),
                'MOM_PERCENT_CHANGE': st.column_config.NumberColumn('MoM Change (%)', format="%.2f")
            },
            use_container_width=True,
            hide_index=True
        )
        
        # Export functionality
        if st.button(f"📥 Export {self.service_name} Data", key=f"export_{self.service_name}_{view_type.value}"):
            csv = filtered_data.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"snowflake_{self.service_name.lower()}_{view_type.value}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
    
    def get_grouping_column(self, view_type: ViewType) -> str:
        """
        Get the database column name for the specified view type.
        
        Args:
            view_type (ViewType): View type enum
            
        Returns:
            str: Database column name
        """
        return self.view_types[view_type]['column']
    
    def process_service_data(self, raw_data: pd.DataFrame, view_type: ViewType) -> pd.DataFrame:
        """
        Process raw service data using DataProcessor utilities.
        
        Args:
            raw_data (pd.DataFrame): Raw data from database query
            view_type (ViewType): Current view type
            
        Returns:
            pd.DataFrame: Processed and aggregated data
        """
        if raw_data.empty:
            return pd.DataFrame()
        
        # Get the appropriate grouping column
        grouping_column = self.get_grouping_column(view_type)
        
        # Transform data for the view type
        transformed_data = DataProcessor.transform_for_view_type(raw_data, view_type, self.service_name)
        
        # Aggregate monthly consumption
        if 'START_TIME' in transformed_data.columns:
            date_column = 'START_TIME'
        elif 'USAGE_DATE' in transformed_data.columns:
            date_column = 'USAGE_DATE'
        else:
            # Fallback - create a date column if missing
            st.warning(f"No date column found in {self.service_name} data")
            return transformed_data
        
        # Determine credit column
        credit_column = 'CREDITS_USED'
        if 'TOTAL_CREDITS' in transformed_data.columns:
            credit_column = 'TOTAL_CREDITS'
        elif 'CREDITS_USED_COMPUTE' in transformed_data.columns:
            credit_column = 'CREDITS_USED_COMPUTE'
        
        # Aggregate the data
        aggregated_data = DataProcessor.aggregate_monthly_consumption(
            transformed_data, grouping_column, credit_column, date_column
        )
        
        return aggregated_data
    
    def validate_and_format_data(self, data: pd.DataFrame, view_type: ViewType) -> pd.DataFrame:
        """
        Validate and format data for display using DataProcessor utilities.
        
        Args:
            data (pd.DataFrame): Data to validate and format
            view_type (ViewType): Current view type
            
        Returns:
            pd.DataFrame: Validated and formatted data
        """
        if data.empty:
            return data
        
        # Validate data consistency
        required_columns = ['USAGE_MONTH', 'TOTAL_CREDITS', 'GROUP_BY']
        is_valid, issues = DataProcessor.validate_data_consistency(data, required_columns)
        
        if not is_valid:
            st.warning(f"Data validation issues found: {', '.join(issues)}")
        
        # Format display columns
        formatted_data = data.copy()
        
        # Format credits columns
        for col in ['TOTAL_CREDITS', 'COMPUTE_CREDITS', 'CLOUD_SERVICES_CREDITS']:
            if col in formatted_data.columns:
                formatted_data[f'{col}_DISPLAY'] = formatted_data[col].apply(
                    DataProcessor.format_credits_display
                )
        
        # Format date columns
        if 'USAGE_MONTH' in formatted_data.columns:
            formatted_data['USAGE_MONTH_DISPLAY'] = formatted_data['USAGE_MONTH'].dt.strftime('%Y-%m')
        
        return formatted_data
    
    @abstractmethod
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Abstract method to retrieve service-specific data.
        Must be implemented by subclasses.
        
        Args:
            view_type (ViewType): Selected view type
            
        Returns:
            Optional[pd.DataFrame]: Service usage data or None if no data available
        """
        pass
    
    @abstractmethod
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Abstract method to get the base SQL query for the service.
        Must be implemented by subclasses.
        
        Args:
            view_type (ViewType): Selected view type
            
        Returns:
            str: Base SQL query for the service
        """
        pass


# Concrete Service Analyzer Implementations

class StorageAnalyzer(ServiceAnalyzer):
    """
    Storage cost analyzer for monitoring account-level storage usage and costs.
    Analyzes data from STORAGE_USAGE view at the account level.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Storage Analyzer."""
        super().__init__("Storage", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering account-level storage analysis.
        Simplified version without view toggles.
        """
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            return
        
        # Get account-level storage data
        storage_data = self.get_service_data(ViewType.WAREHOUSE)  # Use any view type since we're not using the toggle
        
        # Handle empty result sets with appropriate messaging
        if storage_data is None or storage_data.empty:
            st.warning("No storage data found for your account")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might storage data be empty?**
                
                • **New Account**: Storage data may not be available for very new accounts
                • **No Data**: Account may not have significant storage usage yet
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                
                **Troubleshooting Steps:**
                1. Verify your account has databases with data
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure ACCOUNTADMIN or similar role with usage permissions
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE
                """)
            return
        
        # Render account-level storage analysis
        self.render_account_storage_metrics(storage_data)
        self.render_storage_trends_chart(storage_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get account-level storage usage data.
        
        Args:
            view_type (ViewType): Ignored for storage - always account level
            
        Returns:
            Optional[pd.DataFrame]: Account storage usage data or None if error
        """
        cache_key = "account_storage_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get account-level storage query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading account storage data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load storage data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate account-level storage usage query using DATABASE_STORAGE_USAGE_HISTORY.
        
        This view provides more accurate database-level storage that is aggregated to account level.
        Includes database storage, failsafe storage, stage storage, hybrid tables, and archive storage.
        
        Args:
            view_type (ViewType): Ignored - always account level
            
        Returns:
            str: SQL query for account-level storage data
        """
        return """
        WITH database_storage AS (
            SELECT 
                USAGE_DATE,
                SUM(AVERAGE_DATABASE_BYTES) as STORAGE_BYTES,
                SUM(AVERAGE_FAILSAFE_BYTES) as FAILSAFE_BYTES
            FROM SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY
            GROUP BY USAGE_DATE
        ),
        stage_storage AS (
            SELECT 
                USAGE_DATE,
                SUM(AVERAGE_STAGE_BYTES) as STAGE_BYTES
            FROM SNOWFLAKE.ACCOUNT_USAGE.STAGE_STORAGE_USAGE_HISTORY
            GROUP BY USAGE_DATE
        ),
        account_storage AS (
            SELECT 
                USAGE_DATE,
                HYBRID_TABLE_STORAGE_BYTES,
                ARCHIVE_STORAGE_COOL_BYTES,
                ARCHIVE_STORAGE_COLD_BYTES
            FROM SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE
        )
        SELECT 
            COALESCE(d.USAGE_DATE, s.USAGE_DATE, a.USAGE_DATE) as USAGE_DATE,
            COALESCE(d.STORAGE_BYTES, 0) as STORAGE_BYTES,
            COALESCE(s.STAGE_BYTES, 0) as STAGE_BYTES,
            COALESCE(d.FAILSAFE_BYTES, 0) as FAILSAFE_BYTES,
            COALESCE(a.HYBRID_TABLE_STORAGE_BYTES, 0) as HYBRID_TABLE_BYTES,
            COALESCE(a.ARCHIVE_STORAGE_COOL_BYTES, 0) as ARCHIVE_COOL_BYTES,
            COALESCE(a.ARCHIVE_STORAGE_COLD_BYTES, 0) as ARCHIVE_COLD_BYTES,
            -- Convert bytes to GB for easier reading
            COALESCE(d.STORAGE_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as STORAGE_GB,
            COALESCE(s.STAGE_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as STAGE_GB,
            COALESCE(d.FAILSAFE_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as FAILSAFE_GB,
            COALESCE(a.HYBRID_TABLE_STORAGE_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as HYBRID_TABLE_GB,
            COALESCE(a.ARCHIVE_STORAGE_COOL_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as ARCHIVE_COOL_GB,
            COALESCE(a.ARCHIVE_STORAGE_COLD_BYTES, 0) / (1024.0 * 1024.0 * 1024.0) as ARCHIVE_COLD_GB,
            -- Total storage
            (COALESCE(d.STORAGE_BYTES, 0) + COALESCE(s.STAGE_BYTES, 0) + COALESCE(d.FAILSAFE_BYTES, 0) + 
             COALESCE(a.HYBRID_TABLE_STORAGE_BYTES, 0) + COALESCE(a.ARCHIVE_STORAGE_COOL_BYTES, 0) + 
             COALESCE(a.ARCHIVE_STORAGE_COLD_BYTES, 0)) / (1024.0 * 1024.0 * 1024.0) as TOTAL_STORAGE_GB
        FROM database_storage d
        FULL OUTER JOIN stage_storage s ON d.USAGE_DATE = s.USAGE_DATE
        FULL OUTER JOIN account_storage a ON COALESCE(d.USAGE_DATE, s.USAGE_DATE) = a.USAGE_DATE
        ORDER BY USAGE_DATE DESC
        """
    
    def render_account_storage_metrics(self, data: pd.DataFrame) -> None:
        """
        Render account-level storage metrics.
        
        Args:
            data (pd.DataFrame): Account storage data
        """
        if data.empty:
            return
        
        # Ensure USAGE_DATE is properly converted to datetime
        data = data.copy()
        data['USAGE_DATE'] = pd.to_datetime(data['USAGE_DATE'])
        
        # Get latest storage metrics
        latest_date = data['USAGE_DATE'].max()
        latest_data = data[data['USAGE_DATE'] == latest_date].iloc[0]
        
        # Get previous month for comparison - use date arithmetic that works consistently
        prev_month_date = latest_date - pd.DateOffset(months=1)
        
        # Filter data using date comparison - convert both sides to the same type
        prev_data = data[data['USAGE_DATE'] <= prev_month_date]
        
        if not prev_data.empty:
            prev_latest = prev_data.iloc[0]
            storage_change = ((latest_data['TOTAL_STORAGE_GB'] - prev_latest['TOTAL_STORAGE_GB']) / prev_latest['TOTAL_STORAGE_GB']) * 100 if prev_latest['TOTAL_STORAGE_GB'] > 0 else 0
        else:
            storage_change = 0
        
        st.markdown("#### Account Storage Overview")
        
        # Display metrics in columns
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label="Total Storage",
                value=f"{latest_data['TOTAL_STORAGE_GB']:,.1f} GB",
                delta=f"{storage_change:+.1f}% vs last month" if storage_change != 0 else None,
                help="Total storage across all databases, stages, and failsafe. Percentage shows month-over-month change."
            )
        
        with col2:
            st.metric(
                label="Database Storage",
                value=f"{latest_data['STORAGE_GB']:,.1f} GB",
                help="Primary database storage usage"
            )
        
        with col3:
            st.metric(
                label="Stage Storage",
                value=f"{latest_data['STAGE_GB']:,.1f} GB",
                help="Temporary stage storage for data loading"
            )
        
        with col4:
            st.metric(
                label="Failsafe Storage",
                value=f"{latest_data['FAILSAFE_GB']:,.1f} GB",
                help="Failsafe storage for data recovery"
            )
        
        # Calculate estimated monthly cost using official Snowflake pricing (AWS US East rates)
        # Standard Storage: $23/TB/month, Hybrid Tables: $0.34/GB/month ($348/TB/month)
        # Archive Cool: $4/TB/month, Archive Cold: $1/TB/month
        standard_storage_tb = (latest_data['STORAGE_GB'] + latest_data['STAGE_GB'] + latest_data['FAILSAFE_GB']) / 1024
        hybrid_storage_gb = latest_data.get('HYBRID_TABLE_GB', 0) if 'HYBRID_TABLE_GB' in latest_data else 0
        archive_cool_tb = latest_data.get('ARCHIVE_COOL_GB', 0) / 1024 if 'ARCHIVE_COOL_GB' in latest_data else 0
        archive_cold_tb = latest_data.get('ARCHIVE_COLD_GB', 0) / 1024 if 'ARCHIVE_COLD_GB' in latest_data else 0
        
        estimated_monthly_cost = (
            standard_storage_tb * 23 +  # $23/TB/month for standard
            hybrid_storage_gb * 0.34 +   # $0.34/GB/month for hybrid tables
            archive_cool_tb * 4 +         # $4/TB/month for archive cool
            archive_cold_tb * 1           # $1/TB/month for archive cold
        )
        
        # Get credit price for equivalent calculation
        credit_price = st.session_state.get('credit_price', 2.00)
        credit_equivalent = estimated_monthly_cost / credit_price if credit_price > 0 else 0
        
        # Additional metrics row - Estimated Monthly Cost and Credit Equivalent
        cost_col1, cost_col2 = st.columns(2)
        with cost_col1:
            st.metric(
                label="Est. Monthly Cost",
                value=f"${estimated_monthly_cost:,.2f}",
                help="Estimated monthly storage cost based on Snowflake pricing: Standard \\$23/TB, Hybrid \\$0.34/GB, Archive Cool \\$4/TB, Archive Cold \\$1/TB"
            )
        with cost_col2:
            st.metric(
                label="Credit Equivalent",
                value=f"{credit_equivalent:,.2f}",
                help=f"Storage cost expressed as credit equivalent at \\${credit_price:.2f}/credit"
            )
    
    def render_storage_trends_chart(self, data: pd.DataFrame) -> None:
        """
        Render storage trends chart showing growth over time.
        
        Args:
            data (pd.DataFrame): Account storage data
        """
        if data.empty:
            return
        
        st.markdown("#### Storage Trends Over Time")
        
        # Ensure consistent date handling
        data = data.copy()
        data['USAGE_DATE'] = pd.to_datetime(data['USAGE_DATE'])
        
        # Ensure new columns exist with defaults
        for col in ['HYBRID_TABLE_GB', 'ARCHIVE_COOL_GB', 'ARCHIVE_COLD_GB']:
            if col not in data.columns:
                data[col] = 0
        
        # Aggregate by month for cleaner visualization
        data['MONTH'] = data['USAGE_DATE'].dt.to_period('M')
        monthly_data = data.groupby('MONTH').agg({
            'STORAGE_GB': 'mean',
            'STAGE_GB': 'mean',
            'FAILSAFE_GB': 'mean',
            'HYBRID_TABLE_GB': 'mean',
            'ARCHIVE_COOL_GB': 'mean',
            'ARCHIVE_COLD_GB': 'mean',
            'TOTAL_STORAGE_GB': 'mean'
        }).reset_index()
        monthly_data['MONTH'] = monthly_data['MONTH'].dt.to_timestamp()
        monthly_data = monthly_data.sort_values('MONTH')
        
        # Calculate estimated monthly cost for each month
        # Standard Storage: $23/TB/month, Hybrid Tables: $0.34/GB/month
        # Archive Cool: $4/TB/month, Archive Cold: $1/TB/month
        monthly_data['EST_MONTHLY_COST'] = (
            (monthly_data['STORAGE_GB'] + monthly_data['STAGE_GB'] + monthly_data['FAILSAFE_GB']) / 1024 * 23 +
            monthly_data['HYBRID_TABLE_GB'] * 0.34 +
            monthly_data['ARCHIVE_COOL_GB'] / 1024 * 4 +
            monthly_data['ARCHIVE_COLD_GB'] / 1024 * 1
        )
        
        # Create stacked bar chart
        fig = go.Figure()
        
        # Add invisible trace for Est. Monthly Cost at top of hover
        fig.add_trace(go.Scatter(
            x=monthly_data['MONTH'],
            y=[0] * len(monthly_data),
            mode='markers',
            marker=dict(size=0, opacity=0),
            name='Est. Monthly Cost',
            customdata=monthly_data['EST_MONTHLY_COST'],
            hovertemplate='<b>$%{customdata:,.2f}</b><extra></extra>',
            showlegend=False
        ))
        
        # Add database storage bars
        fig.add_trace(go.Bar(
            x=monthly_data['MONTH'],
            y=monthly_data['STORAGE_GB'],
            name='Database Storage',
            marker_color='#1f77b4',
            hovertemplate='<b>Database Storage</b>: %{y:,.1f} GB<extra></extra>'
        ))
        
        # Add stage storage bars
        fig.add_trace(go.Bar(
            x=monthly_data['MONTH'],
            y=monthly_data['STAGE_GB'],
            name='Stage Storage',
            marker_color='#2ca02c',
            hovertemplate='<b>Stage Storage</b>: %{y:,.1f} GB<extra></extra>'
        ))
        
        # Add failsafe storage bars
        fig.add_trace(go.Bar(
            x=monthly_data['MONTH'],
            y=monthly_data['FAILSAFE_GB'],
            name='Failsafe Storage',
            marker_color='#ff7f0e',
            hovertemplate='<b>Failsafe Storage</b>: %{y:,.1f} GB<extra></extra>'
        ))
        
        # Add hybrid table storage bars (only if there's data)
        if monthly_data['HYBRID_TABLE_GB'].sum() > 0:
            fig.add_trace(go.Bar(
                x=monthly_data['MONTH'],
                y=monthly_data['HYBRID_TABLE_GB'],
                name='Hybrid Tables',
                marker_color='#9467bd',
                hovertemplate='<b>Hybrid Tables</b>: %{y:,.1f} GB<extra></extra>'
            ))
        
        # Add archive cool storage bars (only if there's data)
        if monthly_data['ARCHIVE_COOL_GB'].sum() > 0:
            fig.add_trace(go.Bar(
                x=monthly_data['MONTH'],
                y=monthly_data['ARCHIVE_COOL_GB'],
                name='Archive (Cool)',
                marker_color='#17becf',
                hovertemplate='<b>Archive (Cool)</b>: %{y:,.1f} GB<extra></extra>'
            ))
        
        # Add archive cold storage bars (only if there's data)
        if monthly_data['ARCHIVE_COLD_GB'].sum() > 0:
            fig.add_trace(go.Bar(
                x=monthly_data['MONTH'],
                y=monthly_data['ARCHIVE_COLD_GB'],
                name='Archive (Cold)',
                marker_color='#7f7f7f',
                hovertemplate='<b>Archive (Cold)</b>: %{y:,.1f} GB<extra></extra>'
            ))
        
        # Update layout for stacked bar chart
        fig.update_layout(
            barmode='stack',
            title='Monthly Average Storage Usage',
            xaxis_title='Month',
            yaxis_title='Storage (GB)',
            height=400,
            hovermode='x unified',
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5
            ),
            xaxis=dict(
                tickformat='%b %Y'
            )
        )
        
        render_plotly_chart(fig)
        
        # Sort data by date for insights
        data_sorted = data.sort_values('USAGE_DATE')
        
        # Add storage insights
        with st.expander("**Storage Insights**"):
            st.markdown("**Current Breakdown:**")
            latest_data = data_sorted.iloc[-1]
            total = latest_data['TOTAL_STORAGE_GB']
            if total > 0:
                st.write(f"• **Database**: {(latest_data['STORAGE_GB']/total)*100:.1f}% ({latest_data['STORAGE_GB']:,.1f} GB)")
                st.write(f"• **Stage**: {(latest_data['STAGE_GB']/total)*100:.1f}% ({latest_data['STAGE_GB']:,.1f} GB)")
                st.write(f"• **Failsafe**: {(latest_data['FAILSAFE_GB']/total)*100:.1f}% ({latest_data['FAILSAFE_GB']:,.1f} GB)")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - only trends for account-level storage.
        
        Args:
            data (pd.DataFrame): Storage data
            view_type (ViewType): Ignored for account-level storage
        """
        # This method is not used in the simplified version
        # All rendering is done directly in render_analysis()
        pass


class ConsumptionAnalyzer(ServiceAnalyzer):
    """
    Warehouse consumption analyzer for monitoring compute usage and costs.
    Analyzes data from WAREHOUSE_METERING_HISTORY for warehouse-level insights.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Warehouse Compute Analyzer."""
        super().__init__("Warehouse Compute", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering warehouse consumption analysis.
        Simplified version focusing only on warehouse analysis.
        """
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            return
        
        # Get warehouse consumption data
        consumption_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if consumption_data is None or consumption_data.empty:
            st.warning("No warehouse consumption data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might consumption data be empty?**
                
                • **No Warehouse Usage**: No warehouses have been active recently
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No consumption in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                
                **Troubleshooting Steps:**
                1. Verify warehouses are running and processing queries
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure warehouses are consuming compute credits
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
                """)
            return
        
        # Render warehouse consumption analysis
        self.render_warehouse_consumption_metrics(consumption_data)
        self.render_warehouse_consumption_charts(consumption_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get warehouse consumption data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always warehouse level
            
        Returns:
            Optional[pd.DataFrame]: Warehouse consumption data or None if error
        """
        cache_key = "warehouse_consumption_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get warehouse consumption query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading warehouse consumption data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load consumption data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate warehouse consumption query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for warehouse consumption data
        """
        return """
        SELECT 
            START_TIME,
            WAREHOUSE_NAME,
            CREDITS_USED_COMPUTE,
            CREDITS_USED_CLOUD_SERVICES,
            CREDITS_USED_COMPUTE + CREDITS_USED_CLOUD_SERVICES as TOTAL_CREDITS,
            CREDITS_USED_COMPUTE as COMPUTE_CREDITS,
            CREDITS_USED_CLOUD_SERVICES as CLOUD_SERVICES_CREDITS,
            WAREHOUSE_ID
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND WAREHOUSE_ID > 0  -- Skip pseudo-VWs such as "CLOUD_SERVICES_ONLY"
          AND WAREHOUSE_NAME IS NOT NULL
          AND (CREDITS_USED_COMPUTE > 0 OR CREDITS_USED_CLOUD_SERVICES > 0)
        ORDER BY START_TIME DESC, WAREHOUSE_NAME
        """
    
    def render_warehouse_consumption_metrics(self, data: pd.DataFrame) -> None:
        """
        Render warehouse consumption metrics using official Snowflake patterns.
        
        Args:
            data (pd.DataFrame): Warehouse consumption data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Calculate summary metrics
        total_compute_credits = data['CREDITS_USED_COMPUTE'].sum()
        total_cloud_services_credits = data['CREDITS_USED_CLOUD_SERVICES'].sum()
        total_credits = total_compute_credits + total_cloud_services_credits
        unique_warehouses = data['WAREHOUSE_NAME'].nunique()
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['TOTAL_CREDITS'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['TOTAL_CREDITS'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        st.markdown("#### Warehouse Consumption Overview")
        st.caption("Last 12 months of warehouse compute usage")
        
        # Display metrics in columns - based on official Snowflake patterns
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric(
                label="Total Compute Credits (12 mo)",
                value=format_credits_with_dollars(total_compute_credits),
                help="Total compute credits consumed by all warehouses over the last 12 months"
            )
        
        with col2:
            current_month_str = current_month.strftime('%B %Y')
            prev_month_str = prev_month.strftime('%B %Y')
            st.metric(
                label=f"{current_month_str} (MTD)",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}% vs {prev_month_str}" if mom_change != 0 else None,
                delta_color="inverse",
                help=f"Month-to-date credits for {current_month_str}. Comparison to {prev_month_str} may be incomplete if current month is in progress."
            )
        
        with col3:
            st.metric(
                label="Active Warehouses",
                value=f"{unique_warehouses}",
                help="Number of warehouses with consumption"
            )
        
        # Additional metrics row
        col1, col2 = st.columns(2)
        
        with col1:
            avg_daily_credits = data.groupby(data['START_TIME'].dt.date)['TOTAL_CREDITS'].sum().mean()
            st.metric(
                label="Daily Average",
                value=format_credits_with_dollars(avg_daily_credits),
                help="Average daily credit consumption"
            )
        
        with col2:
            # Find peak day
            daily_consumption = data.groupby(data['START_TIME'].dt.date)['TOTAL_CREDITS'].sum()
            peak_daily = daily_consumption.max()
            st.metric(
                label="Peak Daily Usage",
                value=format_credits_with_dollars(peak_daily),
                help="Highest single-day credit consumption"
            )
    
    def render_warehouse_consumption_charts(self, data: pd.DataFrame) -> None:
        """
        Render warehouse consumption charts and analysis.
        
        Args:
            data (pd.DataFrame): Warehouse consumption data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Create tabs for different analyses
        tab1, tab2, tab3 = st.tabs(["Trends", "By Warehouse", "Daily Patterns"])
        
        with tab1:
            self.render_consumption_trends_chart(data)
        
        with tab2:
            self.render_warehouse_breakdown_chart(data)
        
        with tab3:
            self.render_daily_patterns_chart(data)
    
    def render_consumption_trends_chart(self, data: pd.DataFrame) -> None:
        """Render consumption trends over time."""
        st.markdown("#### Warehouse Consumption Over Time")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED_COMPUTE': 'sum'
        }).reset_index()
        
        # Create trends chart
        fig = go.Figure()
        
        # Add compute credits line
        fig.add_trace(go.Scatter(
            x=daily_data['START_TIME'],
            y=daily_data['CREDITS_USED_COMPUTE'],
            mode='lines',
            name='Compute Credits',
            line=dict(color='#1f77b4', width=3),
            hovertemplate='<b>Compute Credits</b><br>Date: %{x}<br>Credits: %{y:,.0f}<extra></extra>'
        ))
        
        fig.update_layout(
            title='Daily Warehouse Credit Consumption',
            xaxis_title='Date',
            yaxis_title='Credits Used',
            height=500,
            hovermode='x unified'
        )
        
        # Add time range information to chart
        update_chart_with_time_range(
            fig, 
            daily_data, 
            'START_TIME', 
            'Date', 
            'Daily Warehouse Credit Consumption'
        )
        
        render_plotly_chart(fig)
    
    def render_warehouse_breakdown_chart(self, data: pd.DataFrame) -> None:
        """Render breakdown by warehouse."""
        # Calculate and display time range
        if 'START_TIME' in data.columns and len(data) > 0:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            date_range_str = f"{min_date.strftime('%b %d, %Y')} - {max_date.strftime('%b %d, %Y')}"
            st.markdown(f"#### Credit Consumption by Warehouse")
            st.caption(f"Data period: {date_range_str}")
        else:
            st.markdown("#### Credit Consumption by Warehouse")
        
        # Aggregate by warehouse
        warehouse_data = data.groupby('WAREHOUSE_NAME').agg({
            'CREDITS_USED_COMPUTE': 'sum',
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'TOTAL_CREDITS': 'sum'
        }).reset_index().sort_values('TOTAL_CREDITS', ascending=False)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Bar chart of top warehouses
            top_warehouses = warehouse_data.head(10)
            fig_bar = px.bar(
                top_warehouses,
                x='TOTAL_CREDITS',
                y='WAREHOUSE_NAME',
                orientation='h',
                title='Top 10 Warehouses by Credit Consumption',
                labels={'TOTAL_CREDITS': 'Credits Used', 'WAREHOUSE_NAME': 'Warehouse'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Pie chart of warehouse distribution
            fig_pie = px.pie(
                warehouse_data.head(8),  # Top 8 for readability
                values='TOTAL_CREDITS',
                names='WAREHOUSE_NAME',
                title='Credit Distribution by Warehouse (Top 8)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        # Weekly cost breakdown by warehouse (stacked bar chart)
        st.markdown("#### Weekly Cost by Warehouse")
        
        # Get credit price from session state
        credit_price = st.session_state.get('credit_price', 2.0)
        
        # Group by week and warehouse
        weekly_warehouse_data = data.copy()
        weekly_warehouse_data['WEEK'] = weekly_warehouse_data['START_TIME'].dt.to_period('W').apply(lambda x: x.start_time)
        weekly_warehouse_data['WEEK_LABEL'] = weekly_warehouse_data['WEEK'].dt.strftime('%b %d')
        
        weekly_agg = weekly_warehouse_data.groupby(['WEEK', 'WEEK_LABEL', 'WAREHOUSE_NAME']).agg({
            'TOTAL_CREDITS': 'sum'
        }).reset_index()
        
        # Calculate cost
        weekly_agg['COST'] = weekly_agg['TOTAL_CREDITS'] * credit_price
        
        # Get warehouse list sorted by total cost
        warehouse_totals = weekly_agg.groupby('WAREHOUSE_NAME')['COST'].sum().sort_values(ascending=False)
        all_warehouses = warehouse_totals.index.tolist()
        
        # Warehouse selector - default to top 5 by cost
        default_warehouses = all_warehouses[:5] if len(all_warehouses) > 5 else all_warehouses
        selected_warehouses = st.multiselect(
            "Select Warehouses",
            options=all_warehouses,
            default=default_warehouses,
            key="weekly_cost_warehouse_selector"
        )
        
        if not selected_warehouses:
            st.info("Select at least one warehouse to display the chart.")
            return
        
        # Filter to selected warehouses
        chart_data = weekly_agg[weekly_agg['WAREHOUSE_NAME'].isin(selected_warehouses)].copy()
        chart_data = chart_data.sort_values('WEEK')
        
        # Create stacked bar chart
        fig_stacked = px.bar(
            chart_data,
            x='WEEK_LABEL',
            y='COST',
            color='WAREHOUSE_NAME',
            title=f'Weekly Warehouse Cost (${credit_price:.2f}/credit)',
            labels={'COST': 'Cost ($)', 'WEEK_LABEL': 'Week', 'WAREHOUSE_NAME': 'Warehouse'}
        )
        fig_stacked.update_layout(
            height=450,
            barmode='stack',
            xaxis_tickangle=-45,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
        )
        fig_stacked.update_yaxes(tickprefix='$', tickformat=',.0f')
        render_plotly_chart(fig_stacked)
    
    def render_daily_patterns_chart(self, data: pd.DataFrame) -> None:
        """Render daily usage patterns."""
        st.markdown("#### Daily Usage Patterns")
        st.caption("Aggregated patterns across the last 12 months")
        
        credit_price = st.session_state.get('credit_price', 3.0)
        
        data['HOUR'] = data['START_TIME'].dt.hour
        data['DAY_OF_WEEK'] = data['START_TIME'].dt.day_name()
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        
        hourly_stats = data.groupby('HOUR')['TOTAL_CREDITS'].agg(['mean', 'sum']).reset_index()
        daily_stats = data.groupby('DAY_OF_WEEK')['TOTAL_CREDITS'].agg(['mean', 'sum']).reindex(day_order).reset_index()
        
        col1, col2 = st.columns(2)
        
        with col1:
            hourly_stats['COST'] = hourly_stats['mean'] * credit_price
            max_cost = hourly_stats['COST'].max()
            hourly_stats['intensity'] = hourly_stats['COST'] / max_cost
            colors = [f'rgba(59, 130, 246, {0.3 + 0.7 * i})' for i in hourly_stats['intensity']]
            
            fig_hourly = go.Figure(data=go.Bar(
                x=hourly_stats['HOUR'],
                y=hourly_stats['COST'],
                marker_color=colors,
                hovertemplate='Hour %{x}:00<br>Avg Cost: $%{y:,.2f}<extra></extra>'
            ))
            fig_hourly.update_layout(
                title='Average Hourly Cost Distribution',
                xaxis_title='Hour of Day',
                yaxis_title='Average Cost ($)',
                height=350,
                xaxis=dict(tickmode='linear', dtick=2),
                yaxis=dict(tickprefix='$', tickformat=',.0f')
            )
            render_plotly_chart(fig_hourly)
        
        with col2:
            daily_stats['COST'] = daily_stats['sum'] * credit_price
            max_daily = daily_stats['COST'].max()
            daily_stats['intensity'] = daily_stats['COST'] / max_daily
            colors_daily = [f'rgba(34, 197, 94, {0.3 + 0.7 * i})' for i in daily_stats['intensity']]
            
            fig_daily = go.Figure(data=go.Bar(
                x=daily_stats['DAY_OF_WEEK'],
                y=daily_stats['COST'],
                marker_color=colors_daily,
                hovertemplate='%{x}<br>Total Cost: $%{y:,.2f}<extra></extra>'
            ))
            fig_daily.update_layout(
                title='Total Cost by Day of Week',
                xaxis_title='',
                yaxis_title='Total Cost ($)',
                height=350,
                xaxis=dict(tickangle=-45),
                yaxis=dict(tickprefix='$', tickformat=',.0f')
            )
            render_plotly_chart(fig_daily)
    
    def render_consumption_insights(self, data: pd.DataFrame) -> None:
        """Render consumption insights and recommendations."""
        st.markdown("#### Consumption Insights & Recommendations")
        
        # Calculate key metrics for insights
        warehouse_data = data.groupby('WAREHOUSE_NAME').agg({
            'CREDITS_USED_COMPUTE': 'sum',
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'TOTAL_CREDITS': 'sum'
        }).reset_index()
        
        # High cloud services usage analysis
        warehouse_data['CLOUD_SERVICES_PCT'] = (warehouse_data['CREDITS_USED_CLOUD_SERVICES'] / 
                                               warehouse_data['TOTAL_CREDITS'] * 100)
        high_cs_warehouses = warehouse_data[warehouse_data['CLOUD_SERVICES_PCT'] > 10]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_warehouses = len(warehouse_data)
            st.write(f"• **Total Active Warehouses**: {total_warehouses}")
            
            if len(high_cs_warehouses) > 0:
                st.write(f"• **High Cloud Services Usage**: {len(high_cs_warehouses)} warehouses (>{10}%)")
                st.write("  - These warehouses may need investigation")
                
            # Top consumer
            top_warehouse = warehouse_data.iloc[0] if not warehouse_data.empty else None
            if top_warehouse is not None:
                st.write(f"• **Top Consumer**: {top_warehouse['WAREHOUSE_NAME']}")
                st.write(f"  - {top_warehouse['TOTAL_CREDITS']:,.0f} credits total")
        
        with col2:
            st.markdown("**Recommendations:**")
            
            if len(high_cs_warehouses) > 0:
                st.write("• **Investigate High Cloud Services Usage**:")
                for _, wh in high_cs_warehouses.head(3).iterrows():
                    st.write(f"  - {wh['WAREHOUSE_NAME']}: {wh['CLOUD_SERVICES_PCT']:.1f}%")
            
            # Usage patterns
            avg_daily = data.groupby(data['START_TIME'].dt.date)['TOTAL_CREDITS'].sum().mean()
            peak_daily = data.groupby(data['START_TIME'].dt.date)['TOTAL_CREDITS'].sum().max()
            
            if peak_daily > avg_daily * 2:
                st.write("• **High Variability Detected**")
                st.write("  - Consider auto-scaling or scheduled scaling")
            
            st.write("• **Regular Monitoring**:")
            st.write("  - Track daily usage patterns")
            st.write("  - Monitor warehouse efficiency")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in warehouse-focused version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified warehouse version
        # All rendering is done directly in render_analysis()
        pass


class CloudServicesAnalyzer(ServiceAnalyzer):
    """
    Cloud Services analyzer for monitoring overhead costs and cloud service usage.
    Analyzes cloud services credits from QUERY_HISTORY for overhead cost monitoring.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Cloud Services Analyzer."""
        super().__init__("Cloud Services", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering cloud services overhead analysis.
        Simplified version focusing on cloud services overhead monitoring.
        """
        st.markdown("#### Cloud Services Credits")
        st.caption("Last 12 months of cloud services overhead")
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            return
        
        # Get cloud services data
        cloud_services_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if cloud_services_data is None or cloud_services_data.empty:
            st.warning("No cloud services usage data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might cloud services data be empty?**
                
                • **No Query Activity**: No queries have been executed recently
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No cloud services usage in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                
                **Troubleshooting Steps:**
                1. Verify queries are being executed that use cloud services
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure queries are generating cloud services credits
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY (CREDITS_USED_CLOUD_SERVICES)
                """)
            return
        
        # Render cloud services analysis
        self.render_cloud_services_metrics(cloud_services_data)
        self.render_cloud_services_charts(cloud_services_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get cloud services usage data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always focuses on cloud services overhead
            
        Returns:
            Optional[pd.DataFrame]: Cloud services usage data or None if error
        """
        cache_key = "cloud_services_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get cloud services query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading cloud services data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"Failed to load cloud services data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate cloud services usage query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for cloud services data
        """
        return """
        SELECT 
            START_TIME,
            WAREHOUSE_NAME,
            USER_NAME,
            QUERY_TYPE,
            EXECUTION_STATUS,
            CREDITS_USED_CLOUD_SERVICES,
            CREDITS_USED_CLOUD_SERVICES as TOTAL_CREDITS,
            0 as COMPUTE_CREDITS,
            CREDITS_USED_CLOUD_SERVICES as CLOUD_SERVICES_CREDITS,
            TOTAL_ELAPSED_TIME,
            BYTES_SCANNED,
            QUERY_ID
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND CREDITS_USED_CLOUD_SERVICES > 0
          AND EXECUTION_STATUS = 'SUCCESS'
        ORDER BY START_TIME DESC, CREDITS_USED_CLOUD_SERVICES DESC
        """
    
    def render_cloud_services_metrics(self, data: pd.DataFrame) -> None:
        """
        Render cloud services overhead metrics.
        
        Args:
            data (pd.DataFrame): Cloud services usage data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Calculate summary metrics
        total_cloud_services_credits = data['CREDITS_USED_CLOUD_SERVICES'].sum()
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['CREDITS_USED_CLOUD_SERVICES'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['CREDITS_USED_CLOUD_SERVICES'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric(
                label="Total Cloud Services Credits (12 mo)",
                value=format_credits_with_dollars(total_cloud_services_credits),
                help="Total overhead credits for metadata, compilation, and cloud services over the last 12 months"
            )
        
        with col2:
            prev_month_str = prev_month.strftime('%b %Y')
            current_month_str = current_month.strftime('%b %Y')
            delta_help = f"Change from {prev_month_str} to {current_month_str}"
            st.metric(
                label=f"Current Month ({current_month_str})",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}% vs {prev_month_str}" if mom_change != 0 else None,
                help=delta_help
            )
    
    def render_cloud_services_charts(self, data: pd.DataFrame) -> None:
        """
        Render cloud services charts and analysis.
        
        Args:
            data (pd.DataFrame): Cloud services data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Create tabs for different analyses
        tab1, tab2 = st.tabs(["Trends", "By Query Type"])
        
        with tab1:
            self.render_cloud_services_trends_chart(data)
        
        with tab2:
            self.render_query_type_analysis(data)
    
    def render_cloud_services_trends_chart(self, data: pd.DataFrame) -> None:
        """Render cloud services trends over time."""
        st.markdown("#### Cloud Services Usage Trends")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count'
        }).reset_index()
        daily_data.rename(columns={'QUERY_ID': 'QUERY_COUNT'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Daily credits chart
            fig_credits = px.line(
                daily_data,
                x='START_TIME',
                y='CREDITS_USED_CLOUD_SERVICES',
                title='Daily Cloud Services Credits',
                labels={'START_TIME': 'Date', 'CREDITS_USED_CLOUD_SERVICES': 'Credits Used'}
            )
            fig_credits.update_traces(line=dict(color='#ff7f0e', width=3))
            fig_credits.update_layout(height=400)
            render_plotly_chart(fig_credits)
        
        with col2:
            # Daily query count
            fig_queries = px.line(
                daily_data,
                x='START_TIME',
                y='QUERY_COUNT',
                title='Daily Query Count (with Cloud Services)',
                labels={'START_TIME': 'Date', 'QUERY_COUNT': 'Number of Queries'}
            )
            fig_queries.update_traces(line=dict(color='#2ca02c', width=3))
            fig_queries.update_layout(height=400)
            render_plotly_chart(fig_queries)
        

    
    def render_warehouse_cloud_services_chart(self, data: pd.DataFrame) -> None:
        """Render cloud services breakdown by warehouse."""
        st.markdown("#### Cloud Services by Warehouse")
        
        # Aggregate by warehouse
        warehouse_data = data.groupby('WAREHOUSE_NAME').agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count'
        }).reset_index().sort_values('CREDITS_USED_CLOUD_SERVICES', ascending=False)
        warehouse_data.rename(columns={'QUERY_ID': 'QUERY_COUNT'}, inplace=True)
        warehouse_data['CREDITS_PER_QUERY'] = warehouse_data['CREDITS_USED_CLOUD_SERVICES'] / warehouse_data['QUERY_COUNT']
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top warehouses by cloud services credits
            top_warehouses = warehouse_data.head(10)
            fig_bar = px.bar(
                top_warehouses,
                x='CREDITS_USED_CLOUD_SERVICES',
                y='WAREHOUSE_NAME',
                orientation='h',
                title='Top 10 Warehouses by Cloud Services Credits',
                labels={'CREDITS_USED_CLOUD_SERVICES': 'Credits Used', 'WAREHOUSE_NAME': 'Warehouse'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Credits per query by warehouse
            fig_efficiency = px.bar(
                top_warehouses,
                x='CREDITS_PER_QUERY',
                y='WAREHOUSE_NAME',
                orientation='h',
                title='Cloud Services Efficiency by Warehouse',
                labels={'CREDITS_PER_QUERY': 'Credits per Query', 'WAREHOUSE_NAME': 'Warehouse'}
            )
            fig_efficiency.update_layout(height=400)
            render_plotly_chart(fig_efficiency)
        
        # Detailed warehouse table
        st.markdown("#### Warehouse Cloud Services Details")
        
        display_data = warehouse_data.copy()
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED_CLOUD_SERVICES'].apply(lambda x: f"{x:,.4f}")
        display_data['CREDITS_PER_QUERY_FORMATTED'] = display_data['CREDITS_PER_QUERY'].apply(lambda x: f"{x:,.6f}")
        
        st.dataframe(
            display_data[['WAREHOUSE_NAME', 'CREDITS_FORMATTED', 'QUERY_COUNT', 'CREDITS_PER_QUERY_FORMATTED']],
            column_config={
                'WAREHOUSE_NAME': 'Warehouse Name',
                'CREDITS_FORMATTED': 'Total Cloud Services Credits',
                'QUERY_COUNT': 'Query Count',
                'CREDITS_PER_QUERY_FORMATTED': 'Credits per Query'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_query_type_analysis(self, data: pd.DataFrame) -> None:
        """Render analysis by query type."""
        st.markdown("#### Cloud Services by Query Type")
        
        credit_price = st.session_state.get('credit_price', 3.0)
        
        # Aggregate by query type
        query_type_data = data.groupby('QUERY_TYPE').agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count',
            'TOTAL_ELAPSED_TIME': 'mean'
        }).reset_index().sort_values('CREDITS_USED_CLOUD_SERVICES', ascending=False)
        query_type_data.rename(columns={'QUERY_ID': 'QUERY_COUNT'}, inplace=True)
        query_type_data['CREDITS_PER_QUERY'] = query_type_data['CREDITS_USED_CLOUD_SERVICES'] / query_type_data['QUERY_COUNT']
        query_type_data['COST'] = query_type_data['CREDITS_USED_CLOUD_SERVICES'] * credit_price
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Cost by query type
            fig_type = px.pie(
                query_type_data,
                values='COST',
                names='QUERY_TYPE',
                title='Cloud Services Cost by Query Type'
            )
            fig_type.update_traces(
                textposition='inside', 
                textinfo='percent+label',
                hovertemplate='<b>%{label}</b><br>Cost: $%{value:,.2f}<br>%{percent}<extra></extra>'
            )
            render_plotly_chart(fig_type)
        
        with col2:
            # Cost bar chart by type
            fig_cost = px.bar(
                query_type_data,
                x='QUERY_TYPE',
                y='COST',
                title='Cost by Query Type',
                labels={'QUERY_TYPE': 'Query Type', 'COST': 'Cost ($)'}
            )
            fig_cost.update_layout(xaxis_tickangle=-45, height=400)
            fig_cost.update_traces(
                marker_color='#ff7f0e',
                hovertemplate='<b>%{x}</b><br>Cost: $%{y:,.2f}<extra></extra>'
            )
            render_plotly_chart(fig_cost)
        
        # Query type efficiency table
        st.markdown("#### Query Type Analysis")
        
        display_data = query_type_data.copy()
        display_data['COST_FORMATTED'] = display_data['COST'].apply(lambda x: f"${x:,.2f}")
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED_CLOUD_SERVICES'].apply(lambda x: f"{x:,.4f}")
        display_data['CREDITS_PER_QUERY_FORMATTED'] = display_data['CREDITS_PER_QUERY'].apply(lambda x: f"{x:,.6f}")
        display_data['AVG_ELAPSED_TIME'] = display_data['TOTAL_ELAPSED_TIME'].apply(lambda x: f"{x:,.0f} ms" if not pd.isna(x) else "N/A")
        
        st.dataframe(
            display_data[['QUERY_TYPE', 'COST_FORMATTED', 'CREDITS_FORMATTED', 'QUERY_COUNT', 'CREDITS_PER_QUERY_FORMATTED', 'AVG_ELAPSED_TIME']],
            column_config={
                'QUERY_TYPE': 'Query Type',
                'COST_FORMATTED': 'Cost',
                'CREDITS_FORMATTED': 'Total Credits',
                'QUERY_COUNT': 'Query Count',
                'CREDITS_PER_QUERY_FORMATTED': 'Credits per Query',
                'AVG_ELAPSED_TIME': 'Avg Elapsed Time'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_cloud_services_optimization(self, data: pd.DataFrame) -> None:
        """Render cloud services optimization recommendations."""
        st.markdown("#### Cloud Services Optimization")
        
        # Calculate optimization metrics
        warehouse_data = data.groupby('WAREHOUSE_NAME').agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count',
            'TOTAL_ELAPSED_TIME': 'mean'
        }).reset_index()
        warehouse_data['CREDITS_PER_QUERY'] = warehouse_data['CREDITS_USED_CLOUD_SERVICES'] / warehouse_data['QUERY_ID']
        
        # High overhead warehouses
        high_overhead_warehouses = warehouse_data[warehouse_data['CREDITS_PER_QUERY'] > warehouse_data['CREDITS_PER_QUERY'].quantile(0.75)]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_credits = data['CREDITS_USED_CLOUD_SERVICES'].sum()
            total_queries = len(data)
            avg_credits_per_query = total_credits / total_queries if total_queries > 0 else 0
            
            st.write(f"• **Total Overhead**: {total_credits:,.4f} credits")
            st.write(f"• **Average per Query**: {avg_credits_per_query:.6f} credits")
            st.write(f"• **Queries Analyzed**: {total_queries:,}")
            
            if len(high_overhead_warehouses) > 0:
                st.write(f"• **High Overhead Warehouses**: {len(high_overhead_warehouses)}")
                st.write("  - Above 75th percentile for credits per query")
        
        with col2:
            st.markdown("**Optimization Recommendations:**")
            
            # Query type analysis for recommendations
            query_types = data['QUERY_TYPE'].value_counts()
            
            if 'SELECT' in query_types.index and query_types['SELECT'] > query_types.sum() * 0.7:
                st.write("• **High SELECT Query Volume**:")
                st.write("  - Consider result caching optimization")
                st.write("  - Review query patterns for redundancy")
            
            if len(high_overhead_warehouses) > 0:
                st.write("• **High Overhead Warehouses**:")
                for _, wh in high_overhead_warehouses.head(3).iterrows():
                    st.write(f"  - {wh['WAREHOUSE_NAME']}: {wh['CREDITS_PER_QUERY']:.6f} credits/query")
            
            avg_elapsed_time = data['TOTAL_ELAPSED_TIME'].mean()
            if not pd.isna(avg_elapsed_time) and avg_elapsed_time > 10000:  # 10 seconds
                st.write("• **High Query Elapsed Time**:")
                st.write("  - Consider query optimization")
                st.write("  - Review query complexity and patterns")
            
            st.write("• **General Recommendations**:")
            st.write("  - Monitor cloud services overhead trends")
            st.write("  - Optimize frequently-run queries")
            st.write("  - Use query result caching when possible")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in cloud services version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified cloud services version
        # All rendering is done directly in render_analysis()
        pass


class ReplicationAnalyzer(ServiceAnalyzer):
    """
    Replication analyzer for monitoring replication costs and data sharing expenses.
    Analyzes replication usage from REPLICATION_GROUP_USAGE_HISTORY for cost monitoring.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Replication Analyzer."""
        super().__init__("Replication", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering replication cost analysis.
        Simplified version focusing on replication and data sharing costs.
        """
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("No active Snowflake session available")
            return
        
        # Get replication data
        replication_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if replication_data is None or replication_data.empty:
            st.warning("No replication usage data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might replication data be empty?**
                
                • **No Replication Activity**: No replication groups are configured or active
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No replication activity in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                • **Feature Usage**: Replication may not be enabled for this account
                
                **Troubleshooting Steps:**
                1. Verify replication groups are configured and active
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure replication features are enabled for your account
                5. Verify data sharing or cross-region replication is in use
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.REPLICATION_GROUP_USAGE_HISTORY
                """)
            return
        
        # Render replication analysis
        self.render_replication_metrics(replication_data)
        self.render_replication_charts(replication_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get replication usage data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always focuses on replication costs
            
        Returns:
            Optional[pd.DataFrame]: Replication usage data or None if error
        """
        cache_key = "replication_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get replication query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading replication data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load replication data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate replication usage query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for replication data
        """
        return """
        SELECT 
            START_TIME,
            END_TIME,
            REPLICATION_GROUP_NAME,
            CREDITS_USED,
            CREDITS_USED as TOTAL_CREDITS,
            0 as COMPUTE_CREDITS,
            CREDITS_USED as REPLICATION_CREDITS,
            BYTES_TRANSFERRED,
            -- Calculate duration in hours
            DATEDIFF('second', START_TIME, END_TIME) / 3600.0 as DURATION_HOURS
        FROM SNOWFLAKE.ACCOUNT_USAGE.REPLICATION_GROUP_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND CREDITS_USED > 0
        ORDER BY START_TIME DESC, CREDITS_USED DESC
        """
    
    def render_replication_metrics(self, data: pd.DataFrame) -> None:
        """
        Render replication cost metrics.
        
        Args:
            data (pd.DataFrame): Replication usage data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        data['END_TIME'] = pd.to_datetime(data['END_TIME'])
        
        # Calculate summary metrics
        total_replication_credits = data['CREDITS_USED'].sum()
        total_bytes_transferred = data['BYTES_TRANSFERRED'].sum()
        unique_replication_groups = data['REPLICATION_GROUP_NAME'].nunique()
        total_replication_sessions = len(data)
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['CREDITS_USED'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['CREDITS_USED'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        st.markdown("#### Replication Cost Overview")
        
        # Display metrics in columns
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label="Total Replication Credits",
                value=format_credits_with_dollars(total_replication_credits),
                help="Total credits used for data replication and sharing"
            )
        
        with col2:
            st.metric(
                label="Current Month",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}%" if mom_change != 0 else None,
                help=f"Replication credits for {current_month}"
            )
        
        with col3:
            st.metric(
                label="Data Transferred",
                value=f"{total_bytes_transferred / (1024**3):,.1f} GB",
                help="Total data transferred through replication"
            )
        
        with col4:
            st.metric(
                label="Replication Groups",
                value=f"{unique_replication_groups}",
                help="Number of active replication groups"
            )
        
        # Additional metrics row
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label="Replication Sessions",
                value=f"{total_replication_sessions:,}",
                help="Total replication sessions executed"
            )
        
        with col2:
            avg_credits_per_session = total_replication_credits / total_replication_sessions if total_replication_sessions > 0 else 0
            st.metric(
                label="Avg Credits/Session",
                value=format_credits_with_dollars(avg_credits_per_session),
                help="Average credits per replication session"
            )
        
        with col3:
            avg_bytes_per_session = total_bytes_transferred / total_replication_sessions if total_replication_sessions > 0 else 0
            st.metric(
                label="Avg Data/Session",
                value=f"{avg_bytes_per_session / (1024**2):,.1f} MB",
                help="Average data transferred per session"
            )
        

    
    def render_replication_charts(self, data: pd.DataFrame) -> None:
        """
        Render replication charts and analysis.
        
        Args:
            data (pd.DataFrame): Replication data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Create tabs for different analyses
        tab1, tab2 = st.tabs(["Trends", "By Group"])
        
        with tab1:
            self.render_replication_trends_chart(data)
        
        with tab2:
            self.render_replication_group_chart(data)
    
    def render_replication_trends_chart(self, data: pd.DataFrame) -> None:
        """Render replication trends over time."""
        st.markdown("#### Replication Usage Trends")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED': 'sum',
            'BYTES_TRANSFERRED': 'sum',
            'REPLICATION_GROUP_NAME': 'nunique'
        }).reset_index()
        daily_data.rename(columns={'REPLICATION_GROUP_NAME': 'ACTIVE_GROUPS'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Daily credits chart
            fig_credits = px.line(
                daily_data,
                x='START_TIME',
                y='CREDITS_USED',
                title='Daily Replication Credits',
                labels={'START_TIME': 'Date', 'CREDITS_USED': 'Credits Used'}
            )
            fig_credits.update_traces(line=dict(color='#9467bd', width=3))
            fig_credits.update_layout(height=400)
            render_plotly_chart(fig_credits)
        
        with col2:
            # Daily data transfer
            daily_data['BYTES_TRANSFERRED_GB'] = daily_data['BYTES_TRANSFERRED'] / (1024**3)
            fig_transfer = px.line(
                daily_data,
                x='START_TIME',
                y='BYTES_TRANSFERRED_GB',
                title='Daily Data Transfer (GB)',
                labels={'START_TIME': 'Date', 'BYTES_TRANSFERRED_GB': 'Data Transferred (GB)'}
            )
            fig_transfer.update_traces(line=dict(color='#8c564b', width=3))
            fig_transfer.update_layout(height=400)
            render_plotly_chart(fig_transfer)
        

    
    def render_replication_group_chart(self, data: pd.DataFrame) -> None:
        """Render replication breakdown by group."""
        st.markdown("#### Replication by Group")
        
        # Aggregate by replication group
        group_data = data.groupby('REPLICATION_GROUP_NAME').agg({
            'CREDITS_USED': 'sum',
            'BYTES_TRANSFERRED': 'sum',
            'START_TIME': 'count'
        }).reset_index().sort_values('CREDITS_USED', ascending=False)
        group_data.rename(columns={'START_TIME': 'SESSION_COUNT'}, inplace=True)
        group_data['BYTES_TRANSFERRED_GB'] = group_data['BYTES_TRANSFERRED'] / (1024**3)
        group_data['CREDITS_PER_SESSION'] = group_data['CREDITS_USED'] / group_data['SESSION_COUNT']
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top groups by credits
            fig_bar = px.bar(
                group_data.head(10),
                x='CREDITS_USED',
                y='REPLICATION_GROUP_NAME',
                orientation='h',
                title='Top 10 Replication Groups by Credits',
                labels={'CREDITS_USED': 'Credits Used', 'REPLICATION_GROUP_NAME': 'Replication Group'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Credits distribution pie chart
            fig_pie = px.pie(
                group_data.head(8),  # Top 8 for readability
                values='CREDITS_USED',
                names='REPLICATION_GROUP_NAME',
                title='Credit Distribution by Group (Top 8)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        # Detailed group table
        st.markdown("#### Replication Group Details")
        
        credit_price = st.session_state.get('credit_price', 3.0)
        display_data = group_data.copy()
        display_data['COST'] = display_data['CREDITS_USED'] * credit_price
        display_data['COST_FORMATTED'] = display_data['COST'].apply(lambda x: f"${x:,.2f}")
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED'].apply(lambda x: f"{x:,.4f}")
        display_data['DATA_TRANSFERRED_FORMATTED'] = display_data['BYTES_TRANSFERRED_GB'].apply(lambda x: f"{x:,.2f} GB")
        display_data['CREDITS_PER_SESSION_FORMATTED'] = display_data['CREDITS_PER_SESSION'].apply(lambda x: f"{x:,.6f}")
        
        st.dataframe(
            display_data[['REPLICATION_GROUP_NAME', 'COST_FORMATTED', 'CREDITS_FORMATTED', 'DATA_TRANSFERRED_FORMATTED', 
                         'SESSION_COUNT', 'CREDITS_PER_SESSION_FORMATTED']],
            column_config={
                'REPLICATION_GROUP_NAME': 'Replication Group',
                'COST_FORMATTED': 'Total Cost',
                'CREDITS_FORMATTED': 'Total Credits',
                'DATA_TRANSFERRED_FORMATTED': 'Data Transferred',
                'SESSION_COUNT': 'Sessions',
                'CREDITS_PER_SESSION_FORMATTED': 'Credits per Session'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_data_transfer_analysis(self, data: pd.DataFrame) -> None:
        """Render data transfer analysis."""
        st.markdown("#### Data Transfer Analysis")
        
        # Convert bytes to GB for better readability
        data = data.copy()
        data['BYTES_TRANSFERRED_GB'] = data['BYTES_TRANSFERRED'] / (1024**3)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Data transfer vs credits scatter plot
            fig_scatter = px.scatter(
                data,
                x='BYTES_TRANSFERRED_GB',
                y='CREDITS_USED',
                color='REPLICATION_GROUP_NAME',
                title='Data Transfer vs Credits Used',
                labels={'BYTES_TRANSFERRED_GB': 'Data Transferred (GB)', 'CREDITS_USED': 'Credits Used'},
                hover_data=['DURATION_HOURS']
            )
            fig_scatter.update_layout(height=400)
            render_plotly_chart(fig_scatter)
        
        with col2:
            # Duration analysis
            fig_duration = px.histogram(
                data,
                x='DURATION_HOURS',
                title='Replication Session Duration Distribution',
                labels={'DURATION_HOURS': 'Duration (Hours)', 'count': 'Number of Sessions'},
                nbins=20
            )
            fig_duration.update_layout(height=400)
            render_plotly_chart(fig_duration)
        
        # Transfer efficiency analysis
        st.markdown("#### Transfer Efficiency Analysis")
        
        # Calculate efficiency metrics
        data['EFFICIENCY'] = data['BYTES_TRANSFERRED_GB'] / data['CREDITS_USED']
        data['EFFICIENCY'] = data['EFFICIENCY'].replace([float('inf'), -float('inf')], 0)
        
        efficiency_stats = data.groupby('REPLICATION_GROUP_NAME')['EFFICIENCY'].agg(['mean', 'std', 'count']).reset_index()
        efficiency_stats.columns = ['REPLICATION_GROUP_NAME', 'AVG_EFFICIENCY', 'STD_EFFICIENCY', 'SESSION_COUNT']
        efficiency_stats = efficiency_stats.sort_values('AVG_EFFICIENCY', ascending=False)
        
        fig_efficiency = px.bar(
            efficiency_stats.head(10),
            x='AVG_EFFICIENCY',
            y='REPLICATION_GROUP_NAME',
            orientation='h',
            title='Average Transfer Efficiency by Group (GB per Credit)',
            labels={'AVG_EFFICIENCY': 'Average Efficiency (GB/Credit)', 'REPLICATION_GROUP_NAME': 'Replication Group'}
        )
        fig_efficiency.update_layout(height=400)
        render_plotly_chart(fig_efficiency)
    
    def render_replication_optimization(self, data: pd.DataFrame) -> None:
        """Render replication optimization recommendations."""
        st.markdown("#### Replication Optimization")
        
        # Calculate optimization metrics
        group_data = data.groupby('REPLICATION_GROUP_NAME').agg({
            'CREDITS_USED': 'sum',
            'BYTES_TRANSFERRED': 'sum',
            'START_TIME': 'count',
            'DURATION_HOURS': 'mean'
        }).reset_index()
        group_data['BYTES_TRANSFERRED_GB'] = group_data['BYTES_TRANSFERRED'] / (1024**3)
        group_data['CREDITS_PER_SESSION'] = group_data['CREDITS_USED'] / group_data['START_TIME']
        group_data['EFFICIENCY'] = group_data['BYTES_TRANSFERRED_GB'] / group_data['CREDITS_USED']
        group_data['EFFICIENCY'] = group_data['EFFICIENCY'].replace([float('inf'), -float('inf')], 0)
        
        # High cost groups
        high_cost_groups = group_data[group_data['CREDITS_USED'] > group_data['CREDITS_USED'].quantile(0.75)]
        
        # Low efficiency groups
        low_efficiency_groups = group_data[group_data['EFFICIENCY'] < group_data['EFFICIENCY'].quantile(0.25)]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_credits = data['CREDITS_USED'].sum()
            total_data_gb = data['BYTES_TRANSFERRED'].sum() / (1024**3)
            total_sessions = len(data)
            avg_efficiency = total_data_gb / total_credits if total_credits > 0 else 0
            
            st.write(f"• **Total Replication Cost**: {total_credits:,.4f} credits")
            st.write(f"• **Total Data Transferred**: {total_data_gb:,.2f} GB")
            st.write(f"• **Average Efficiency**: {avg_efficiency:.2f} GB/credit")
            st.write(f"• **Total Sessions**: {total_sessions:,}")
            
            if len(high_cost_groups) > 0:
                st.write(f"• **High Cost Groups**: {len(high_cost_groups)}")
                st.write("  - Above 75th percentile for total credits")
        
        with col2:
            st.markdown("**Optimization Recommendations:**")
            
            if len(low_efficiency_groups) > 0:
                st.write("• **Low Efficiency Groups**:")
                for _, group in low_efficiency_groups.head(3).iterrows():
                    st.write(f"  - {group['REPLICATION_GROUP_NAME']}: {group['EFFICIENCY']:.2f} GB/credit")
                st.write("  - Review replication frequency and data volume")
            
            if len(high_cost_groups) > 0:
                st.write("• **High Cost Groups**:")
                for _, group in high_cost_groups.head(3).iterrows():
                    st.write(f"  - {group['REPLICATION_GROUP_NAME']}: {group['CREDITS_USED']:,.2f} credits")
                st.write("  - Consider optimizing replication schedules")
            
            # Duration analysis
            avg_duration = data['DURATION_HOURS'].mean()
            if not pd.isna(avg_duration) and avg_duration > 1:
                st.write("• **Long Duration Sessions**:")
                st.write("  - Consider breaking large transfers into smaller batches")
                st.write("  - Review network and bandwidth optimization")
            
            st.write("• **General Recommendations**:")
            st.write("  - Monitor replication patterns and schedules")
            st.write("  - Optimize data volume and frequency")
            st.write("  - Consider regional placement strategies")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in replication version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified replication version
        # All rendering is done directly in render_analysis()
        pass


class ClusteringAnalyzer(ServiceAnalyzer):
    """
    Clustering analyzer for monitoring automatic clustering costs and optimization.
    Analyzes clustering usage from AUTOMATIC_CLUSTERING_HISTORY for cost monitoring.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Clustering Analyzer."""
        super().__init__("Clustering", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering clustering cost analysis.
        Simplified version focusing on automatic clustering costs and optimization.
        """
        st.caption("Last 12 months")
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("No active Snowflake session available")
            return
        
        # Get clustering data
        clustering_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if clustering_data is None or clustering_data.empty:
            st.warning("No automatic clustering usage data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might clustering data be empty?**
                
                • **No Clustering Activity**: No tables have automatic clustering enabled
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No clustering activity in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                • **Feature Usage**: Automatic clustering may not be enabled for any tables
                
                **Troubleshooting Steps:**
                1. Verify tables have automatic clustering enabled
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure tables are large enough to trigger clustering
                5. Verify clustering keys are defined on tables
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.AUTOMATIC_CLUSTERING_HISTORY
                """)
            return
        
        # Render clustering analysis
        self.render_clustering_metrics(clustering_data)
        self.render_clustering_charts(clustering_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get clustering usage data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always focuses on clustering costs
            
        Returns:
            Optional[pd.DataFrame]: Clustering usage data or None if error
        """
        cache_key = "clustering_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get clustering query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading clustering data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load clustering data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate clustering usage query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for clustering data
        """
        return """
        SELECT 
            START_TIME,
            END_TIME,
            TABLE_NAME,
            SCHEMA_NAME,
            DATABASE_NAME,
            CREDITS_USED,
            CREDITS_USED as TOTAL_CREDITS,
            0 as COMPUTE_CREDITS,
            CREDITS_USED as CLUSTERING_CREDITS,
            NUM_BYTES_RECLUSTERED,
            NUM_ROWS_RECLUSTERED,
            -- Calculate duration in minutes
            DATEDIFF('second', START_TIME, END_TIME) / 60.0 as DURATION_MINUTES,
            -- Create full table name for grouping
            DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME as FULL_TABLE_NAME
        FROM SNOWFLAKE.ACCOUNT_USAGE.AUTOMATIC_CLUSTERING_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND CREDITS_USED > 0
        ORDER BY START_TIME DESC, CREDITS_USED DESC
        """
    
    def render_clustering_metrics(self, data: pd.DataFrame) -> None:
        """
        Render clustering cost metrics.
        
        Args:
            data (pd.DataFrame): Clustering usage data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        data['END_TIME'] = pd.to_datetime(data['END_TIME'])
        
        # Calculate summary metrics
        total_clustering_credits = data['CREDITS_USED'].sum()
        total_bytes_reclustered = data['NUM_BYTES_RECLUSTERED'].sum()
        total_rows_reclustered = data['NUM_ROWS_RECLUSTERED'].sum()
        unique_tables = data['FULL_TABLE_NAME'].nunique()
        total_clustering_operations = len(data)
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['CREDITS_USED'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['CREDITS_USED'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        st.markdown("#### Automatic Clustering Cost Overview")
        
        # Display metrics in columns
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric(
                label="Total Clustering Credits (12 mo)",
                value=format_credits_with_dollars(total_clustering_credits),
                help="Total credits used for automatic clustering operations over the last 12 months"
            )
        
        with col2:
            prev_month_str = prev_month.strftime('%b %Y')
            current_month_str = current_month.strftime('%b %Y')
            st.metric(
                label=f"Current Month ({current_month_str})",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}% vs {prev_month_str}" if mom_change != 0 else None,
                help=f"Change from {prev_month_str} to {current_month_str}"
            )
    
    def render_clustering_charts(self, data: pd.DataFrame) -> None:
        """
        Render clustering charts and analysis.
        
        Args:
            data (pd.DataFrame): Clustering data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Create tabs for different analyses
        tab1, tab2 = st.tabs(["Trends", "By Table"])
        
        with tab1:
            self.render_clustering_trends_chart(data)
        
        with tab2:
            self.render_table_clustering_chart(data)
    
    def render_clustering_trends_chart(self, data: pd.DataFrame) -> None:
        """Render clustering trends over time."""
        st.markdown("#### Clustering Usage Trends")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED': 'sum',
            'NUM_BYTES_RECLUSTERED': 'sum',
            'NUM_ROWS_RECLUSTERED': 'sum',
            'FULL_TABLE_NAME': 'nunique'
        }).reset_index()
        daily_data.rename(columns={'FULL_TABLE_NAME': 'TABLES_CLUSTERED'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Daily credits chart
            fig_credits = px.line(
                daily_data,
                x='START_TIME',
                y='CREDITS_USED',
                title='Daily Clustering Credits',
                labels={'START_TIME': 'Date', 'CREDITS_USED': 'Credits Used'}
            )
            fig_credits.update_traces(line=dict(color='#17becf', width=3))
            fig_credits.update_layout(height=400)
            render_plotly_chart(fig_credits)
        
        with col2:
            # Daily data reclustered
            daily_data['BYTES_RECLUSTERED_GB'] = daily_data['NUM_BYTES_RECLUSTERED'] / (1024**3)
            fig_data = px.line(
                daily_data,
                x='START_TIME',
                y='BYTES_RECLUSTERED_GB',
                title='Daily Data Reclustered (GB)',
                labels={'START_TIME': 'Date', 'BYTES_RECLUSTERED_GB': 'Data Reclustered (GB)'}
            )
            fig_data.update_traces(line=dict(color='#bcbd22', width=3))
            fig_data.update_layout(height=400)
            render_plotly_chart(fig_data)
        

    
    def render_table_clustering_chart(self, data: pd.DataFrame) -> None:
        """Render clustering breakdown by table."""
        st.markdown("#### Clustering by Table")
        
        # Aggregate by table
        table_data = data.groupby('FULL_TABLE_NAME').agg({
            'CREDITS_USED': 'sum',
            'NUM_BYTES_RECLUSTERED': 'sum',
            'NUM_ROWS_RECLUSTERED': 'sum',
            'START_TIME': 'count'
        }).reset_index().sort_values('CREDITS_USED', ascending=False)
        table_data.rename(columns={'START_TIME': 'OPERATION_COUNT'}, inplace=True)
        table_data['BYTES_RECLUSTERED_GB'] = table_data['NUM_BYTES_RECLUSTERED'] / (1024**3)
        table_data['CREDITS_PER_OPERATION'] = table_data['CREDITS_USED'] / table_data['OPERATION_COUNT']
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top tables by credits
            top_tables = table_data.head(10)
            fig_bar = px.bar(
                top_tables,
                x='CREDITS_USED',
                y='FULL_TABLE_NAME',
                orientation='h',
                title='Top 10 Tables by Clustering Credits',
                labels={'CREDITS_USED': 'Credits Used', 'FULL_TABLE_NAME': 'Table'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Credits distribution pie chart
            fig_pie = px.pie(
                table_data.head(8),  # Top 8 for readability
                values='CREDITS_USED',
                names='FULL_TABLE_NAME',
                title='Credit Distribution by Table (Top 8)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        # Detailed table breakdown
        st.markdown("#### Table Clustering Details")
        
        credit_price = st.session_state.get('credit_price', 3.0)
        display_data = table_data.copy()
        display_data['COST'] = display_data['CREDITS_USED'] * credit_price
        display_data['COST_FORMATTED'] = display_data['COST'].apply(lambda x: f"${x:,.2f}")
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED'].apply(lambda x: f"{x:,.4f}")
        display_data['DATA_RECLUSTERED_FORMATTED'] = display_data['BYTES_RECLUSTERED_GB'].apply(lambda x: f"{x:,.2f} GB")
        display_data['ROWS_RECLUSTERED_FORMATTED'] = display_data['NUM_ROWS_RECLUSTERED'].apply(lambda x: f"{x:,.0f}")
        display_data['CREDITS_PER_OP_FORMATTED'] = display_data['CREDITS_PER_OPERATION'].apply(lambda x: f"{x:,.6f}")
        
        st.dataframe(
            display_data[['FULL_TABLE_NAME', 'COST_FORMATTED', 'CREDITS_FORMATTED', 'DATA_RECLUSTERED_FORMATTED', 
                         'ROWS_RECLUSTERED_FORMATTED', 'OPERATION_COUNT', 'CREDITS_PER_OP_FORMATTED']],
            column_config={
                'FULL_TABLE_NAME': 'Table Name',
                'COST_FORMATTED': 'Total Cost',
                'CREDITS_FORMATTED': 'Total Credits',
                'DATA_RECLUSTERED_FORMATTED': 'Data Reclustered',
                'ROWS_RECLUSTERED_FORMATTED': 'Rows Reclustered',
                'OPERATION_COUNT': 'Operations',
                'CREDITS_PER_OP_FORMATTED': 'Credits per Operation'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_clustering_operations_analysis(self, data: pd.DataFrame) -> None:
        """Render clustering operations analysis."""
        st.markdown("#### Clustering Operations Analysis")
        
        # Convert bytes to GB for better readability
        data = data.copy()
        data['BYTES_RECLUSTERED_GB'] = data['NUM_BYTES_RECLUSTERED'] / (1024**3)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Data reclustered vs credits scatter plot
            fig_scatter = px.scatter(
                data,
                x='BYTES_RECLUSTERED_GB',
                y='CREDITS_USED',
                color='FULL_TABLE_NAME',
                title='Data Reclustered vs Credits Used',
                labels={'BYTES_RECLUSTERED_GB': 'Data Reclustered (GB)', 'CREDITS_USED': 'Credits Used'},
                hover_data=['NUM_ROWS_RECLUSTERED', 'DURATION_MINUTES']
            )
            fig_scatter.update_layout(height=400, showlegend=False)  # Hide legend for readability
            render_plotly_chart(fig_scatter)
        
        with col2:
            # Duration analysis
            fig_duration = px.histogram(
                data,
                x='DURATION_MINUTES',
                title='Clustering Operation Duration Distribution',
                labels={'DURATION_MINUTES': 'Duration (Minutes)', 'count': 'Number of Operations'},
                nbins=20
            )
            fig_duration.update_layout(height=400)
            render_plotly_chart(fig_duration)
        
        # Operation size analysis
        st.markdown("#### Operation Size Analysis")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Rows reclustered distribution
            data['ROWS_RECLUSTERED_MILLIONS'] = data['NUM_ROWS_RECLUSTERED'] / 1_000_000
            fig_rows = px.histogram(
                data,
                x='ROWS_RECLUSTERED_MILLIONS',
                title='Rows Reclustered Distribution (Millions)',
                labels={'ROWS_RECLUSTERED_MILLIONS': 'Rows Reclustered (Millions)', 'count': 'Number of Operations'},
                nbins=15
            )
            fig_rows.update_layout(height=400)
            render_plotly_chart(fig_rows)
        
        with col2:
            # Credits vs rows efficiency
            data['ROWS_PER_CREDIT'] = data['NUM_ROWS_RECLUSTERED'] / data['CREDITS_USED']
            data['ROWS_PER_CREDIT'] = data['ROWS_PER_CREDIT'].replace([float('inf'), -float('inf')], 0)
            
            fig_efficiency = px.scatter(
                data,
                x='CREDITS_USED',
                y='ROWS_PER_CREDIT',
                title='Clustering Efficiency: Rows per Credit',
                labels={'CREDITS_USED': 'Credits Used', 'ROWS_PER_CREDIT': 'Rows per Credit'},
                hover_data=['FULL_TABLE_NAME']
            )
            fig_efficiency.update_layout(height=400)
            render_plotly_chart(fig_efficiency)
    
    def render_clustering_optimization(self, data: pd.DataFrame) -> None:
        """Render clustering optimization recommendations."""
        st.markdown("#### Clustering Optimization")
        
        # Calculate optimization metrics
        table_data = data.groupby('FULL_TABLE_NAME').agg({
            'CREDITS_USED': 'sum',
            'NUM_BYTES_RECLUSTERED': 'sum',
            'NUM_ROWS_RECLUSTERED': 'sum',
            'START_TIME': 'count',
            'DURATION_MINUTES': 'mean'
        }).reset_index()
        table_data['BYTES_RECLUSTERED_GB'] = table_data['NUM_BYTES_RECLUSTERED'] / (1024**3)
        table_data['CREDITS_PER_OPERATION'] = table_data['CREDITS_USED'] / table_data['START_TIME']
        table_data['EFFICIENCY'] = table_data['BYTES_RECLUSTERED_GB'] / table_data['CREDITS_USED']
        table_data['EFFICIENCY'] = table_data['EFFICIENCY'].replace([float('inf'), -float('inf')], 0)
        
        # High cost tables
        high_cost_tables = table_data[table_data['CREDITS_USED'] > table_data['CREDITS_USED'].quantile(0.75)]
        
        # Low efficiency tables
        low_efficiency_tables = table_data[table_data['EFFICIENCY'] < table_data['EFFICIENCY'].quantile(0.25)]
        
        # Frequent clustering tables
        frequent_clustering_tables = table_data[table_data['START_TIME'] > table_data['START_TIME'].quantile(0.75)]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_credits = data['CREDITS_USED'].sum()
            total_data_gb = data['NUM_BYTES_RECLUSTERED'].sum() / (1024**3)
            total_operations = len(data)
            avg_efficiency = total_data_gb / total_credits if total_credits > 0 else 0
            
            st.write(f"• **Total Clustering Cost**: {total_credits:,.4f} credits")
            st.write(f"• **Total Data Reclustered**: {total_data_gb:,.2f} GB")
            st.write(f"• **Average Efficiency**: {avg_efficiency:.2f} GB/credit")
            st.write(f"• **Total Operations**: {total_operations:,}")
            
            if len(high_cost_tables) > 0:
                st.write(f"• **High Cost Tables**: {len(high_cost_tables)}")
                st.write("  - Above 75th percentile for total credits")
                
            if len(frequent_clustering_tables) > 0:
                st.write(f"• **Frequently Clustered Tables**: {len(frequent_clustering_tables)}")
                st.write("  - Above 75th percentile for operation count")
        
        with col2:
            st.markdown("**Optimization Recommendations:**")
            
            if len(low_efficiency_tables) > 0:
                st.write("• **Low Efficiency Tables**:")
                for _, table in low_efficiency_tables.head(3).iterrows():
                    table_short = table['FULL_TABLE_NAME'].split('.')[-1]  # Just table name
                    st.write(f"  - {table_short}: {table['EFFICIENCY']:.2f} GB/credit")
                st.write("  - Review clustering keys and table structure")
            
            if len(high_cost_tables) > 0:
                st.write("• **High Cost Tables**:")
                for _, table in high_cost_tables.head(3).iterrows():
                    table_short = table['FULL_TABLE_NAME'].split('.')[-1]
                    st.write(f"  - {table_short}: {table['CREDITS_USED']:,.2f} credits")
                st.write("  - Consider manual clustering or key optimization")
            
            if len(frequent_clustering_tables) > 0:
                st.write("• **Frequently Clustered Tables**:")
                for _, table in frequent_clustering_tables.head(3).iterrows():
                    table_short = table['FULL_TABLE_NAME'].split('.')[-1]
                    st.write(f"  - {table_short}: {table['START_TIME']} operations")
                st.write("  - Review data loading patterns and clustering keys")
            
            st.write("• **General Recommendations**:")
            st.write("  - Monitor clustering frequency and costs")
            st.write("  - Optimize clustering keys based on query patterns")
            st.write("  - Consider disabling clustering for small tables")
            st.write("  - Review data loading and update patterns")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in clustering version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified clustering version
        # All rendering is done directly in render_analysis()
        pass


class ServerlessAnalyzer(ServiceAnalyzer):
    """
    Serverless analyzer for monitoring serverless computing costs and task optimization.
    Analyzes serverless usage from SERVERLESS_TASK_HISTORY for cost monitoring.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Serverless Analyzer."""
        super().__init__("Serverless", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering serverless cost analysis.
        Simplified version focusing on serverless computing costs and optimization.
        """
        st.caption("Last 12 months")
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("No active Snowflake session available")
            return
        
        # Get serverless data
        serverless_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if serverless_data is None or serverless_data.empty:
            st.warning("No serverless computing usage data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might serverless data be empty?**
                
                • **No Serverless Activity**: No serverless tasks have been executed
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No serverless activity in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                • **Feature Usage**: Serverless computing may not be enabled or used
                
                **Troubleshooting Steps:**
                1. Verify serverless tasks are configured and running
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure serverless features are enabled for your account
                5. Verify tasks are using serverless compute resources
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY
                """)
            return
        
        # Render serverless analysis
        self.render_serverless_metrics(serverless_data)
        self.render_serverless_charts(serverless_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get serverless usage data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always focuses on serverless costs
            
        Returns:
            Optional[pd.DataFrame]: Serverless usage data or None if error
        """
        cache_key = "serverless_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get serverless query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading serverless data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load serverless data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate serverless usage query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for serverless data
        """
        return """
        SELECT 
            START_TIME,
            END_TIME,
            TASK_NAME,
            SCHEMA_NAME,
            DATABASE_NAME,
            CREDITS_USED,
            CREDITS_USED as TOTAL_CREDITS,
            0 as COMPUTE_CREDITS,
            CREDITS_USED as SERVERLESS_CREDITS,
            -- Calculate duration in minutes
            DATEDIFF('second', START_TIME, END_TIME) / 60.0 as DURATION_MINUTES,
            -- Create full task name for grouping
            DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TASK_NAME as FULL_TASK_NAME
        FROM SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND CREDITS_USED > 0
        ORDER BY START_TIME DESC, CREDITS_USED DESC
        """
    
    def render_serverless_metrics(self, data: pd.DataFrame) -> None:
        """
        Render serverless cost metrics.
        
        Args:
            data (pd.DataFrame): Serverless usage data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        data['END_TIME'] = pd.to_datetime(data['END_TIME'])
        
        # Calculate summary metrics
        total_serverless_credits = data['CREDITS_USED'].sum()
        unique_tasks = data['FULL_TASK_NAME'].nunique()
        total_task_executions = len(data)
        avg_duration = data['DURATION_MINUTES'].mean()
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['CREDITS_USED'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['CREDITS_USED'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        st.markdown("#### Serverless Computing Cost Overview")
        
        # Display metrics in columns
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric(
                label="Total Serverless Credits (12 mo)",
                value=format_credits_with_dollars(total_serverless_credits),
                help="Total credits used for serverless task executions over the last 12 months"
            )
        
        with col2:
            prev_month_str = prev_month.strftime('%b %Y')
            current_month_str = current_month.strftime('%b %Y')
            st.metric(
                label=f"Current Month ({current_month_str})",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}% vs {prev_month_str}" if mom_change != 0 else None,
                help=f"Change from {prev_month_str} to {current_month_str}"
            )
    
    def render_serverless_charts(self, data: pd.DataFrame) -> None:
        """
        Render serverless charts and analysis.
        
        Args:
            data (pd.DataFrame): Serverless data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Create tabs for different analyses
        tab1, tab2 = st.tabs(["Trends", "By Task"])
        
        with tab1:
            self.render_serverless_trends_chart(data)
        
        with tab2:
            self.render_task_serverless_chart(data)
    
    def render_serverless_trends_chart(self, data: pd.DataFrame) -> None:
        """Render serverless trends over time."""
        st.markdown("#### Serverless Usage Trends")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED': 'sum',
            'FULL_TASK_NAME': 'nunique',
            'TASK_NAME': 'count',
            'DURATION_MINUTES': 'mean'
        }).reset_index()
        daily_data.rename(columns={'FULL_TASK_NAME': 'UNIQUE_TASKS', 'TASK_NAME': 'EXECUTIONS'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Daily credits chart
            fig_credits = px.line(
                daily_data,
                x='START_TIME',
                y='CREDITS_USED',
                title='Daily Serverless Credits',
                labels={'START_TIME': 'Date', 'CREDITS_USED': 'Credits Used'}
            )
            fig_credits.update_traces(line=dict(color='#2ca02c', width=3))
            fig_credits.update_layout(height=400)
            render_plotly_chart(fig_credits)
        
        with col2:
            # Daily executions
            fig_executions = px.line(
                daily_data,
                x='START_TIME',
                y='EXECUTIONS',
                title='Daily Task Executions',
                labels={'START_TIME': 'Date', 'EXECUTIONS': 'Number of Executions'}
            )
            fig_executions.update_traces(line=dict(color='#d62728', width=3))
            fig_executions.update_layout(height=400)
            render_plotly_chart(fig_executions)
        

    
    def render_task_serverless_chart(self, data: pd.DataFrame) -> None:
        """Render serverless breakdown by task."""
        st.markdown("#### Serverless by Task")
        
        # Aggregate by task
        task_data = data.groupby('FULL_TASK_NAME').agg({
            'CREDITS_USED': 'sum',
            'START_TIME': 'count',
            'DURATION_MINUTES': 'mean'
        }).reset_index().sort_values('CREDITS_USED', ascending=False)
        task_data.rename(columns={'START_TIME': 'EXECUTION_COUNT'}, inplace=True)
        task_data['CREDITS_PER_EXECUTION'] = task_data['CREDITS_USED'] / task_data['EXECUTION_COUNT']
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top tasks by credits
            top_tasks = task_data.head(10)
            fig_bar = px.bar(
                top_tasks,
                x='CREDITS_USED',
                y='FULL_TASK_NAME',
                orientation='h',
                title='Top 10 Tasks by Serverless Credits',
                labels={'CREDITS_USED': 'Credits Used', 'FULL_TASK_NAME': 'Task'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Credits distribution pie chart
            fig_pie = px.pie(
                task_data.head(8),  # Top 8 for readability
                values='CREDITS_USED',
                names='FULL_TASK_NAME',
                title='Credit Distribution by Task (Top 8)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        # Detailed task breakdown
        st.markdown("#### Task Execution Details")
        
        credit_price = st.session_state.get('credit_price', 3.0)
        display_data = task_data.copy()
        display_data['COST'] = display_data['CREDITS_USED'] * credit_price
        display_data['COST_FORMATTED'] = display_data['COST'].apply(lambda x: f"${x:,.2f}")
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED'].apply(lambda x: f"{x:,.4f}")
        display_data['DURATION_FORMATTED'] = display_data['DURATION_MINUTES'].apply(lambda x: f"{x:.1f} min")
        display_data['CREDITS_PER_EXEC_FORMATTED'] = display_data['CREDITS_PER_EXECUTION'].apply(lambda x: f"{x:,.6f}")
        
        st.dataframe(
            display_data[['FULL_TASK_NAME', 'COST_FORMATTED', 'CREDITS_FORMATTED', 'EXECUTION_COUNT', 
                         'DURATION_FORMATTED', 'CREDITS_PER_EXEC_FORMATTED']],
            column_config={
                'FULL_TASK_NAME': 'Task Name',
                'COST_FORMATTED': 'Total Cost',
                'CREDITS_FORMATTED': 'Total Credits',
                'EXECUTION_COUNT': 'Executions',
                'DURATION_FORMATTED': 'Avg Duration',
                'CREDITS_PER_EXEC_FORMATTED': 'Credits per Execution'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_serverless_performance_analysis(self, data: pd.DataFrame) -> None:
        """Render serverless performance analysis."""
        st.markdown("#### ⏱Serverless Performance Analysis")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Replace problematic scatter plot with summary metrics
            st.markdown("#### Performance Summary")
            
            # Calculate summary statistics
            avg_duration = data['DURATION_MINUTES'].mean()
            avg_credits = data['CREDITS_USED'].mean()
            max_duration = data['DURATION_MINUTES'].max()
            max_credits = data['CREDITS_USED'].max()
            
            # Display as metrics
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("Avg Duration", f"{avg_duration:.1f} min")
                st.metric("Max Duration", f"{max_duration:.1f} min")
            with col_b:
                st.metric("Avg Credits", f"{avg_credits:.4f}")
                st.metric("Max Credits", f"{max_credits:.4f}")
            
            # Show top 5 longest running tasks
            st.markdown("**Longest Running Tasks:**")
            top_duration = data.nlargest(5, 'DURATION_MINUTES')[['TASK_NAME', 'DURATION_MINUTES', 'CREDITS_USED']]
            for _, row in top_duration.iterrows():
                st.write(f"• {row['TASK_NAME']}: {row['DURATION_MINUTES']:.1f} min ({row['CREDITS_USED']:.4f} credits)")
        
        with col2:
            # Duration distribution
            fig_duration = px.histogram(
                data,
                x='DURATION_MINUTES',
                title='Task Duration Distribution',
                labels={'DURATION_MINUTES': 'Duration (Minutes)', 'count': 'Number of Executions'},
                nbins=20
            )
            fig_duration.update_layout(height=400)
            # Use helper function to avoid WebGL issues
            render_plotly_chart(fig_duration)
        
        # Performance efficiency analysis
        st.markdown("#### Performance Efficiency Analysis")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Credits distribution
            fig_credits_dist = px.histogram(
                data,
                x='CREDITS_USED',
                title='Credits per Execution Distribution',
                labels={'CREDITS_USED': 'Credits Used', 'count': 'Number of Executions'},
                nbins=15
            )
            fig_credits_dist.update_layout(height=400)
            # Use helper function to avoid WebGL issues
            render_plotly_chart(fig_credits_dist)
        
        with col2:
            # Efficiency by task (credits per minute)
            data['CREDITS_PER_MINUTE'] = data['CREDITS_USED'] / data['DURATION_MINUTES']
            data['CREDITS_PER_MINUTE'] = data['CREDITS_PER_MINUTE'].replace([float('inf'), -float('inf')], 0)
            
            task_efficiency = data.groupby('FULL_TASK_NAME')['CREDITS_PER_MINUTE'].mean().reset_index()
            task_efficiency = task_efficiency.sort_values('CREDITS_PER_MINUTE', ascending=False).head(10)
            
            fig_efficiency = px.bar(
                task_efficiency,
                x='CREDITS_PER_MINUTE',
                y='FULL_TASK_NAME',
                orientation='h',
                title='Top 10 Tasks by Credits per Minute',
                labels={'CREDITS_PER_MINUTE': 'Credits per Minute', 'FULL_TASK_NAME': 'Task'}
            )
            fig_efficiency.update_layout(height=400)
            # Use helper function to avoid WebGL issues
            render_plotly_chart(fig_efficiency)
    
    def render_serverless_optimization(self, data: pd.DataFrame) -> None:
        """Render serverless optimization recommendations."""
        st.markdown("#### Serverless Optimization")
        
        # Calculate optimization metrics
        task_data = data.groupby('FULL_TASK_NAME').agg({
            'CREDITS_USED': 'sum',
            'START_TIME': 'count',
            'DURATION_MINUTES': 'mean'
        }).reset_index()
        task_data['CREDITS_PER_EXECUTION'] = task_data['CREDITS_USED'] / task_data['START_TIME']
        task_data['CREDITS_PER_MINUTE'] = task_data['CREDITS_USED'] / (task_data['DURATION_MINUTES'] * task_data['START_TIME'])
        task_data['CREDITS_PER_MINUTE'] = task_data['CREDITS_PER_MINUTE'].replace([float('inf'), -float('inf')], 0)
        
        # High cost tasks
        high_cost_tasks = task_data[task_data['CREDITS_USED'] > task_data['CREDITS_USED'].quantile(0.75)]
        
        # Inefficient tasks (high credits per minute)
        inefficient_tasks = task_data[task_data['CREDITS_PER_MINUTE'] > task_data['CREDITS_PER_MINUTE'].quantile(0.75)]
        
        # Frequent execution tasks
        frequent_tasks = task_data[task_data['START_TIME'] > task_data['START_TIME'].quantile(0.75)]
        
        # Long running tasks
        long_running_tasks = task_data[task_data['DURATION_MINUTES'] > task_data['DURATION_MINUTES'].quantile(0.75)]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_credits = data['CREDITS_USED'].sum()
            total_executions = len(data)
            avg_credits_per_execution = total_credits / total_executions if total_executions > 0 else 0
            avg_duration = data['DURATION_MINUTES'].mean()
            
            st.write(f"• **Total Serverless Cost**: {total_credits:,.4f} credits")
            st.write(f"• **Total Executions**: {total_executions:,}")
            st.write(f"• **Average per Execution**: {avg_credits_per_execution:.6f} credits")
            st.write(f"• **Average Duration**: {avg_duration:.1f} minutes")
            
            if len(high_cost_tasks) > 0:
                st.write(f"• **High Cost Tasks**: {len(high_cost_tasks)}")
                st.write("  - Above 75th percentile for total credits")
                
            if len(frequent_tasks) > 0:
                st.write(f"• **Frequently Executed Tasks**: {len(frequent_tasks)}")
                st.write("  - Above 75th percentile for execution count")
        
        with col2:
            st.markdown("**Optimization Recommendations:**")
            
            if len(inefficient_tasks) > 0:
                st.write("• **High Credits per Minute Tasks**:")
                for _, task in inefficient_tasks.head(3).iterrows():
                    task_short = task['FULL_TASK_NAME'].split('.')[-1]  # Just task name
                    st.write(f"  - {task_short}: {task['CREDITS_PER_MINUTE']:.4f} credits/min")
                st.write("  - Review task logic and resource usage")
            
            if len(high_cost_tasks) > 0:
                st.write("• **High Cost Tasks**:")
                for _, task in high_cost_tasks.head(3).iterrows():
                    task_short = task['FULL_TASK_NAME'].split('.')[-1]
                    st.write(f"  - {task_short}: {task['CREDITS_USED']:,.2f} credits")
                st.write("  - Consider optimization or scheduling changes")
            
            if len(long_running_tasks) > 0:
                st.write("• **Long Running Tasks**:")
                for _, task in long_running_tasks.head(3).iterrows():
                    task_short = task['FULL_TASK_NAME'].split('.')[-1]
                    st.write(f"  - {task_short}: {task['DURATION_MINUTES']:.1f} minutes")
                st.write("  - Review task complexity and data processing")
            
            st.write("• **General Recommendations**:")
            st.write("  - Monitor task execution patterns and costs")
            st.write("  - Optimize task logic and resource allocation")
            st.write("  - Consider task scheduling and frequency")
            st.write("  - Review data processing efficiency")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in serverless version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified serverless version
        # All rendering is done directly in render_analysis()
        pass


class ClientConsumptionAnalyzer(ServiceAnalyzer):
    """
    Client Consumption analyzer for monitoring consumption by client applications and tools.
    Analyzes client usage from QUERY_HISTORY for client-based cost monitoring.
    """
    
    def __init__(self, data_manager, cache_ttl: int = 3600):
        """Initialize Client Consumption Analyzer."""
        super().__init__("Client Consumption", data_manager, cache_ttl)
    
    def render_analysis(self) -> None:
        """
        Main entry point for rendering client consumption analysis.
        Simplified version focusing on client application consumption patterns.
        """
        
        # Check connection
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            return
        
        # Get client consumption data
        client_data = self.get_service_data(ViewType.WAREHOUSE)  # Always warehouse view
        
        # Handle empty result sets with appropriate messaging
        if client_data is None or client_data.empty:
            st.warning("No client consumption data found")
            with st.expander("**Possible Reasons & Solutions**"):
                st.markdown("""
                **Why might client consumption data be empty?**
                
                • **No Query Activity**: No queries have been executed recently
                • **Data Latency**: Account usage data has up to 3-hour delay
                • **Time Range**: No client activity in the last 12 months
                • **Permissions**: Account may lack access to ACCOUNT_USAGE schema
                • **Client Information**: Client application names may not be captured
                
                **Troubleshooting Steps:**
                1. Verify queries are being executed from various client applications
                2. Check your current role has ACCOUNT_USAGE schema access
                3. Wait for data to propagate (up to 3 hours for recent usage)
                4. Ensure client applications are properly identified in sessions
                5. Verify different tools/applications are connecting to Snowflake
                
                **Data Source:**
                • SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
                • SNOWFLAKE.ACCOUNT_USAGE.SESSIONS
                """)
            return
        
        # Render client consumption analysis
        self.render_client_charts(client_data)
    
    def get_service_data(self, view_type: ViewType) -> Optional[pd.DataFrame]:
        """
        Get client consumption data using official Snowflake patterns.
        
        Args:
            view_type (ViewType): Ignored - always focuses on client consumption
            
        Returns:
            Optional[pd.DataFrame]: Client consumption data or None if error
        """
        cache_key = "client_consumption_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            cache_time = st.session_state.cache_timestamps.get(cache_key, 0)
            if time.time() - cache_time < self.cache_ttl:
                return st.session_state.data_cache[cache_key]
        
        # Get client consumption query
        query = self.get_base_query(view_type)
        
        try:
            with st.spinner("Loading client consumption data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    st.session_state.cache_timestamps[cache_key] = time.time()
                    
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load client consumption data: {str(e)}")
            return None
    
    def get_base_query(self, view_type: ViewType) -> str:
        """
        Generate client consumption query using official Snowflake documentation patterns.
        
        Returns:
            str: SQL query for client consumption data
        """
        return """
        WITH client_sessions AS (
            SELECT 
                SESSION_ID,
                CLIENT_APPLICATION_ID,
                CLIENT_APPLICATION_VERSION,
                CASE 
                    WHEN CLIENT_APPLICATION_ID LIKE 'JDBC%' THEN 'JDBC Application'
                    WHEN CLIENT_APPLICATION_ID LIKE 'ODBC%' THEN 'ODBC Application'  
                    WHEN CLIENT_APPLICATION_ID LIKE 'Python%' THEN 'Python Connector'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Go %' THEN 'Go Driver'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Node%' THEN 'Node.js Driver'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Snowflake UI%' THEN 'Snowflake Web UI'
                    WHEN CLIENT_APPLICATION_ID LIKE 'SnowSQL%' THEN 'SnowSQL CLI'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Tableau%' THEN 'Tableau'
                    WHEN CLIENT_APPLICATION_ID LIKE 'PowerBI%' THEN 'Power BI'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Looker%' THEN 'Looker'
                    WHEN CLIENT_APPLICATION_ID LIKE 'dbt%' THEN 'dbt'
                    WHEN CLIENT_APPLICATION_ID LIKE 'Streamlit%' THEN 'Streamlit'
                    ELSE COALESCE(CLIENT_APPLICATION_ID, 'Unknown Client')
                END as CLIENT_TYPE
            FROM SNOWFLAKE.ACCOUNT_USAGE.SESSIONS
            WHERE CREATED_ON >= DATEADD('month', -12, CURRENT_DATE())
        )
        SELECT 
            q.START_TIME,
            COALESCE(cs.CLIENT_TYPE, 'Unknown Client') as CLIENT_APPLICATION_NAME,
            q.CREDITS_USED_CLOUD_SERVICES,
            q.CREDITS_USED_CLOUD_SERVICES as TOTAL_CREDITS,
            0 as COMPUTE_CREDITS,
            q.CREDITS_USED_CLOUD_SERVICES as CLOUD_SERVICES_CREDITS,
            q.QUERY_TYPE,
            q.WAREHOUSE_NAME,
            q.USER_NAME,
            q.QUERY_ID
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY q
        LEFT JOIN client_sessions cs ON q.SESSION_ID = cs.SESSION_ID
        WHERE q.START_TIME >= DATEADD('month', -12, CURRENT_DATE())
          AND q.CREDITS_USED_CLOUD_SERVICES > 0
          AND q.EXECUTION_STATUS = 'SUCCESS'
        ORDER BY q.START_TIME DESC, q.CREDITS_USED_CLOUD_SERVICES DESC
        """
    
    def render_client_metrics(self, data: pd.DataFrame) -> None:
        """
        Render client consumption metrics.
        
        Args:
            data (pd.DataFrame): Client consumption data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Calculate summary metrics
        total_client_credits = data['CREDITS_USED_CLOUD_SERVICES'].sum()
        unique_clients = data['CLIENT_APPLICATION_NAME'].nunique()
        total_queries = len(data)
        unique_users = data['USER_NAME'].nunique()
        
        # Get current month data for MoM comparison
        current_month = data['START_TIME'].max().to_period('M')
        current_month_data = data[data['START_TIME'].dt.to_period('M') == current_month]
        current_month_credits = current_month_data['CREDITS_USED_CLOUD_SERVICES'].sum()
        
        # Get previous month for comparison
        prev_month = current_month - 1
        prev_month_data = data[data['START_TIME'].dt.to_period('M') == prev_month]
        prev_month_credits = prev_month_data['CREDITS_USED_CLOUD_SERVICES'].sum()
        
        if prev_month_credits > 0:
            mom_change = ((current_month_credits - prev_month_credits) / prev_month_credits) * 100
        else:
            mom_change = 0
        
        st.markdown("#### Client Consumption Overview")
        
        # Display metrics in columns
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label="Total Client Credits",
                value=format_credits_with_dollars(total_client_credits),
                help="Total credits used by all client applications"
            )
        
        with col2:
            st.metric(
                label="Current Month",
                value=format_credits_with_dollars(current_month_credits),
                delta=f"{mom_change:+.1f}%" if mom_change != 0 else None,
                help=f"Client consumption for {current_month}"
            )
        
        with col3:
            st.metric(
                label="Total Queries",
                value=f"{total_queries:,}",
                help="Total queries executed by client applications"
            )
        
        with col4:
            st.metric(
                label="Client Applications",
                value=f"{unique_clients}",
                help="Number of different client application types"
            )
        
        # Additional metrics row
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            avg_credits_per_query = total_client_credits / total_queries if total_queries > 0 else 0
            st.metric(
                label="Avg Credits/Query",
                value=format_credits_with_dollars(avg_credits_per_query),
                help="Average credits per query across all clients"
            )
        
        with col2:
            st.metric(
                label="Active Users",
                value=f"{unique_users}",
                help="Number of users executing queries via clients"
            )
        
        with col3:
            avg_daily_credits = data.groupby(data['START_TIME'].dt.date)['CREDITS_USED_CLOUD_SERVICES'].sum().mean()
            st.metric(
                label="Daily Average",
                value=format_credits_with_dollars(avg_daily_credits),
                help="Average daily client consumption"
            )
        
        with col4:
            # Find peak day
            daily_consumption = data.groupby(data['START_TIME'].dt.date)['CREDITS_USED_CLOUD_SERVICES'].sum()
            peak_daily = daily_consumption.max()
            st.metric(
                label="Peak Daily Usage",
                value=format_credits_with_dollars(peak_daily),
                help="Highest single-day client consumption"
            )
    
    def render_client_charts(self, data: pd.DataFrame) -> None:
        """
        Render client consumption charts and analysis.
        
        Args:
            data (pd.DataFrame): Client consumption data
        """
        if data.empty:
            return
        
        # Ensure consistent date handling
        data = data.copy()
        data['START_TIME'] = pd.to_datetime(data['START_TIME'])
        
        # Render client breakdown directly (no tabs)
        self.render_client_breakdown_chart(data)
    
    def render_client_trends_chart(self, data: pd.DataFrame) -> None:
        """Render client consumption trends over time."""
        st.markdown("#### Client Consumption Trends")
        
        # Daily aggregation
        daily_data = data.groupby(data['START_TIME'].dt.date).agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'CLIENT_APPLICATION_NAME': 'nunique',
            'QUERY_ID': 'count'
        }).reset_index()
        daily_data.rename(columns={'CLIENT_APPLICATION_NAME': 'UNIQUE_CLIENTS', 'QUERY_ID': 'QUERY_COUNT'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Daily credits chart
            fig_credits = px.line(
                daily_data,
                x='START_TIME',
                y='CREDITS_USED_CLOUD_SERVICES',
                title='Daily Client Consumption Credits',
                labels={'START_TIME': 'Date', 'CREDITS_USED_CLOUD_SERVICES': 'Credits Used'}
            )
            fig_credits.update_traces(line=dict(color='#1f77b4', width=3))
            fig_credits.update_layout(height=400)
            render_plotly_chart(fig_credits)
        
        with col2:
            # Daily query count
            fig_queries = px.line(
                daily_data,
                x='START_TIME',
                y='QUERY_COUNT',
                title='Daily Query Count by Clients',
                labels={'START_TIME': 'Date', 'QUERY_COUNT': 'Number of Queries'}
            )
            fig_queries.update_traces(line=dict(color='#ff7f0e', width=3))
            fig_queries.update_layout(height=400)
            render_plotly_chart(fig_queries)
        
        # Credits per query efficiency over time
        daily_data['CREDITS_PER_QUERY'] = daily_data['CREDITS_USED_CLOUD_SERVICES'] / daily_data['QUERY_COUNT']
        daily_data['CREDITS_PER_QUERY'] = daily_data['CREDITS_PER_QUERY'].replace([float('inf'), -float('inf')], 0)
        
        fig_efficiency = px.line(
            daily_data,
            x='START_TIME',
            y='CREDITS_PER_QUERY',
            title='Client Efficiency Over Time (Credits per Query)',
            labels={'START_TIME': 'Date', 'CREDITS_PER_QUERY': 'Credits per Query'}
        )
        fig_efficiency.update_traces(line=dict(color='#2ca02c', width=2))
        fig_efficiency.update_layout(height=400)
        render_plotly_chart(fig_efficiency)
    
    def render_client_breakdown_chart(self, data: pd.DataFrame) -> None:
        """Render client consumption breakdown by application."""
        # Add time range subtitle
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        

        
        # Aggregate by client application
        client_data = data.groupby('CLIENT_APPLICATION_NAME').agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count',
            'USER_NAME': 'nunique'
        }).reset_index().sort_values('CREDITS_USED_CLOUD_SERVICES', ascending=False)
        client_data.rename(columns={'QUERY_ID': 'QUERY_COUNT', 'USER_NAME': 'USER_COUNT'}, inplace=True)
        client_data['CREDITS_PER_QUERY'] = client_data['CREDITS_USED_CLOUD_SERVICES'] / client_data['QUERY_COUNT']
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top clients by credits
            top_clients = client_data.head(10)
            fig_bar = px.bar(
                top_clients,
                x='CREDITS_USED_CLOUD_SERVICES',
                y='CLIENT_APPLICATION_NAME',
                orientation='h',
                title='Top 10 Client Applications by Credits',
                labels={'CREDITS_USED_CLOUD_SERVICES': 'Credits Used', 'CLIENT_APPLICATION_NAME': 'Client Application'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
        
        with col2:
            # Credits distribution pie chart
            fig_pie = px.pie(
                client_data.head(8),  # Top 8 for readability
                values='CREDITS_USED_CLOUD_SERVICES',
                names='CLIENT_APPLICATION_NAME',
                title='Credit Distribution by Client (Top 8)'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        # Detailed client breakdown with total cost
        st.markdown("#### Client Application Details")
        
        credit_price = st.session_state.get('credit_price', 2.00)
        display_data = client_data.copy()
        display_data['TOTAL_COST'] = display_data['CREDITS_USED_CLOUD_SERVICES'] * credit_price
        display_data['CREDITS_FORMATTED'] = display_data['CREDITS_USED_CLOUD_SERVICES'].apply(lambda x: f"{x:,.4f}")
        display_data['COST_FORMATTED'] = display_data['TOTAL_COST'].apply(lambda x: f"${x:,.2f}")
        display_data['CREDITS_PER_QUERY_FORMATTED'] = display_data['CREDITS_PER_QUERY'].apply(lambda x: f"{x:,.6f}")
        
        st.dataframe(
            display_data[['CLIENT_APPLICATION_NAME', 'CREDITS_FORMATTED', 'COST_FORMATTED', 'QUERY_COUNT', 
                         'USER_COUNT', 'CREDITS_PER_QUERY_FORMATTED']],
            column_config={
                'CLIENT_APPLICATION_NAME': 'Client Application',
                'CREDITS_FORMATTED': 'Total Credits',
                'COST_FORMATTED': 'Total Cost',
                'QUERY_COUNT': 'Query Count',
                'USER_COUNT': 'Users',
                'CREDITS_PER_QUERY_FORMATTED': 'Credits per Query'
            },
            use_container_width=True,
            hide_index=True
        )
    
    def render_user_client_analysis(self, data: pd.DataFrame) -> None:
        """Render user-client consumption analysis."""
        st.markdown("#### User and Client Analysis")
        
        # User-client combination analysis
        user_client_data = data.groupby(['USER_NAME', 'CLIENT_APPLICATION_NAME']).agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count'
        }).reset_index().sort_values('CREDITS_USED_CLOUD_SERVICES', ascending=False)
        user_client_data.rename(columns={'QUERY_ID': 'QUERY_COUNT'}, inplace=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Top user-client combinations
            st.markdown("#### 🔝 Top User-Client Combinations")
            top_combinations = user_client_data.head(10)
            
            for _, row in top_combinations.iterrows():
                st.write(f"**{row['USER_NAME']}** via *{row['CLIENT_APPLICATION_NAME']}*")
                st.write(f"  • Credits: {row['CREDITS_USED_CLOUD_SERVICES']:.4f}")
                st.write(f"  • Queries: {row['QUERY_COUNT']:,}")
                st.write("---")
        
        with col2:
            # User diversity by client
            client_user_diversity = data.groupby('CLIENT_APPLICATION_NAME')['USER_NAME'].nunique().reset_index()
            client_user_diversity.rename(columns={'USER_NAME': 'UNIQUE_USERS'}, inplace=True)
            client_user_diversity = client_user_diversity.sort_values('UNIQUE_USERS', ascending=False)
            
            fig_diversity = px.bar(
                client_user_diversity.head(10),
                x='UNIQUE_USERS',
                y='CLIENT_APPLICATION_NAME',
                orientation='h',
                title='User Diversity by Client Application',
                labels={'UNIQUE_USERS': 'Number of Unique Users', 'CLIENT_APPLICATION_NAME': 'Client Application'}
            )
            fig_diversity.update_layout(height=400)
            render_plotly_chart(fig_diversity)
    
    def render_client_optimization(self, data: pd.DataFrame) -> None:
        """Render client consumption optimization recommendations."""
        st.markdown("#### Client Optimization")
        
        # Calculate optimization metrics
        client_data = data.groupby('CLIENT_APPLICATION_NAME').agg({
            'CREDITS_USED_CLOUD_SERVICES': 'sum',
            'QUERY_ID': 'count',
            'USER_NAME': 'nunique'
        }).reset_index()
        client_data['CREDITS_PER_QUERY'] = client_data['CREDITS_USED_CLOUD_SERVICES'] / client_data['QUERY_ID']
        client_data['CREDITS_PER_USER'] = client_data['CREDITS_USED_CLOUD_SERVICES'] / client_data['USER_NAME']
        
        # High consumption clients
        high_consumption_clients = client_data[client_data['CREDITS_USED_CLOUD_SERVICES'] > client_data['CREDITS_USED_CLOUD_SERVICES'].quantile(0.75)]
        
        # Inefficient clients (high credits per query)
        inefficient_clients = client_data[client_data['CREDITS_PER_QUERY'] > client_data['CREDITS_PER_QUERY'].quantile(0.75)]
        
        # High query volume clients
        high_volume_clients = client_data[client_data['QUERY_ID'] > client_data['QUERY_ID'].quantile(0.75)]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Analysis Results:**")
            
            total_credits = data['CREDITS_USED_CLOUD_SERVICES'].sum()
            total_queries = len(data)
            avg_credits_per_query = total_credits / total_queries if total_queries > 0 else 0
            unique_clients = data['CLIENT_APPLICATION_NAME'].nunique()
            
            st.write(f"• **Total Client Consumption**: {total_credits:,.4f} credits")
            st.write(f"• **Total Queries**: {total_queries:,}")
            st.write(f"• **Average per Query**: {avg_credits_per_query:.6f} credits")
            st.write(f"• **Client Applications**: {unique_clients}")
            
            if len(high_consumption_clients) > 0:
                st.write(f"• **High Consumption Clients**: {len(high_consumption_clients)}")
                st.write("  - Above 75th percentile for total credits")
        
        with col2:
            st.markdown("**Optimization Recommendations:**")
            
            if len(inefficient_clients) > 0:
                st.write("• **High Credits per Query Clients**:")
                for _, client in inefficient_clients.head(3).iterrows():
                    st.write(f"  - {client['CLIENT_APPLICATION_NAME']}: {client['CREDITS_PER_QUERY']:.6f} credits/query")
                st.write("  - Review query patterns and optimization")
            
            if len(high_consumption_clients) > 0:
                st.write("• **High Consumption Clients**:")
                for _, client in high_consumption_clients.head(3).iterrows():
                    st.write(f"  - {client['CLIENT_APPLICATION_NAME']}: {client['CREDITS_USED_CLOUD_SERVICES']:,.2f} credits")
                st.write("  - Consider usage optimization strategies")
            
            if len(high_volume_clients) > 0:
                st.write("• **High Query Volume Clients**:")
                for _, client in high_volume_clients.head(3).iterrows():
                    st.write(f"  - {client['CLIENT_APPLICATION_NAME']}: {client['QUERY_ID']:,} queries")
                st.write("  - Review query frequency and caching")
            
            st.write("• **General Recommendations**:")
            st.write("  - Monitor client application usage patterns")
            st.write("  - Optimize query efficiency for high-volume clients")
            st.write("  - Consider connection pooling and caching")
            st.write("  - Review client-specific performance tuning")
    
    def render_analysis_tabs(self, data: pd.DataFrame, view_type: ViewType) -> None:
        """
        Simplified analysis tabs - not used in client consumption version.
        All rendering is done directly in render_analysis().
        """
        # This method is not used in the simplified client consumption version
        # All rendering is done directly in render_analysis()
        pass


class AIServicesAnalyzer:
    """
    Simplified analyzer for Snowflake AI Services usage and costs.
    
    Provides clear, accurate analysis of AI service consumption across six service types:
    - Account-Level AI Services (from METERING_DAILY_HISTORY)
    - Cortex Functions
    - Cortex Analyst
    - Cortex Search
    - Document AI
    - Fine-Tuning
    
    Each service is analyzed independently with simplified visualizations to prevent
    duplicate credit counting and enable easy validation.
    """
    
    def __init__(self, data_manager):
        """
        Initialize AI Services analyzer.
        
        Args:
            data_manager: Instance of DataAccessManager for query execution
        """
        self.data_manager = data_manager
        self.service_name = "AI Services"
    
    def render_analysis(self) -> None:
        """
        Render comprehensive AI services analysis with all service types.
        
        Orchestrates the display of all AI service sections in a logical order:
        1. Account-Level Summary
        2. Cortex Functions
        3. Cortex Analyst
        4. Cortex Search
        5. Document AI
        6. Fine-Tuning
        
        Each section is independent to prevent duplicate credit counting.
        Sections with no data will display informative messages.
        """
        
        # Track if any data exists
        has_any_data = False
        
        # 1. Account-Level AI Services
        account_data = self._get_account_level_data()
        if account_data is not None and not account_data.empty:
            self._render_account_level_section(account_data)
            has_any_data = True
        else:
            self._handle_no_data("Account-Level AI Services")
        
        # 2. Snowflake Intelligence
        si_data = self._get_snowflake_intelligence_data()
        if si_data is not None and not si_data.empty:
            self._render_snowflake_intelligence_section(si_data)
            has_any_data = True
        else:
            self._handle_no_data("Snowflake Intelligence")
        
        # 3. Cortex Agents
        agent_data = self._get_cortex_agents_data()
        if agent_data is not None and not agent_data.empty:
            self._render_cortex_agents_section(agent_data)
            has_any_data = True
        else:
            self._handle_no_data("Cortex Agents")
        
        # 4. Cortex Functions
        functions_data = self._get_cortex_functions_data()
        if functions_data is not None and not functions_data.empty:
            self._render_cortex_functions_section(functions_data)
            has_any_data = True
        else:
            self._handle_no_data("AI Functions")
        
        # 5. Cortex Analyst
        analyst_data = self._get_cortex_analyst_data()
        if analyst_data is not None and not analyst_data.empty:
            self._render_cortex_analyst_section(analyst_data)
            has_any_data = True
        else:
            self._handle_no_data("Cortex Analyst")
        
        # 6. Cortex Search
        search_data = self._get_cortex_search_data()
        if search_data is not None and not search_data.empty:
            self._render_cortex_search_section(search_data)
            has_any_data = True
        else:
            self._handle_no_data("Cortex Search")
        
        # 7. Fine-Tuning
        tuning_data = self._get_fine_tuning_data()
        if tuning_data is not None and not tuning_data.empty:
            self._render_fine_tuning_section(tuning_data)
            has_any_data = True
        else:
            self._handle_no_data("Fine-Tuning")
        
        # 8. Cortex REST API
        rest_api_data = self._get_cortex_rest_api_data()
        if rest_api_data is not None and not rest_api_data.empty:
            self._render_cortex_rest_api_section(rest_api_data)
            has_any_data = True
        else:
            self._handle_no_data("Cortex REST API")
        
        # Display guidance if no AI services have any data
        if not has_any_data:
            st.markdown("---")
            st.info("""
                **No AI Services usage detected**
                
                This could mean:
                - AI Services have not been used in the last 12 months
                - The ACCOUNT_USAGE views may not have data yet (latency up to 45 minutes)
                - The current role may not have access to ACCOUNT_USAGE views
                
                To use AI Services:
                - Try Cortex Functions: `SELECT SNOWFLAKE.CORTEX.COMPLETE('llama2-70b-chat', 'Hello!')`
                - Set up Cortex Search for semantic search capabilities
                - Use Cortex Analyst for natural language data analysis
            """)
    
    def _get_account_level_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve account-level AI services usage data from METERING_DAILY_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY filtered by SERVICE_TYPE='AI_SERVICES'
        to get overall AI services credit consumption at the account level.
        
        Test Query:
        SELECT 
            SERVICE_TYPE,
            USAGE_DATE,
            CREDITS_USED
        FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
        WHERE SERVICE_TYPE = 'AI_SERVICES'
            AND USAGE_DATE >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY USAGE_DATE DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns SERVICE_TYPE, USAGE_DATE, CREDITS_USED,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            SERVICE_TYPE,
            USAGE_DATE,
            CREDITS_USED
        FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
        WHERE SERVICE_TYPE = 'AI_SERVICES'
            AND USAGE_DATE >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY USAGE_DATE DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_account_level"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL credits (convert to 0)
            if 'CREDITS_USED' in data.columns:
                data['CREDITS_USED'] = data['CREDITS_USED'].fillna(0)
            
            # Handle timezone normalization for dates
            if 'USAGE_DATE' in data.columns:
                data['USAGE_DATE'] = pd.to_datetime(data['USAGE_DATE']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            st.error(f"Error querying Account-Level AI Services data: {str(e)}")
            return None
    
    def _render_account_level_section(self, data: pd.DataFrame) -> None:
        """
        Render account-level AI services summary section with metrics, trends, and data table.
        
        Displays:
        - Section header with date range
        - Three key metrics: Total Credits, Daily Average, Last 30 Days
        - Line chart showing credit consumption over time
        - Sortable data table with daily details
        
        Args:
            data: DataFrame with columns SERVICE_TYPE, USAGE_DATE, CREDITS_USED
        """
        # Section header
        st.markdown("---")
        st.markdown("#### Account-Level AI Services")
        
        # Display date range
        if not data.empty and 'USAGE_DATE' in data.columns:
            min_date = data['USAGE_DATE'].min()
            max_date = data['USAGE_DATE'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['CREDITS_USED'].sum()
        num_days = len(data)
        daily_avg = total_credits / num_days if num_days > 0 else 0
        
        # Last 30 days
        last_30_days = data.head(30) if len(data) >= 30 else data
        last_30_credits = last_30_days['CREDITS_USED'].sum()
        
        # Display metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Daily Average", self._format_credits(daily_avg))
        with col3:
            st.metric("Last 30 Days", self._format_credits(last_30_credits))
        
        # Line chart - Credits over time
        st.markdown("##### Credit Consumption Trend")
        
        # Sort by date for proper line chart
        chart_data = data.sort_values('USAGE_DATE')
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=chart_data['USAGE_DATE'],
            y=chart_data['CREDITS_USED'],
            mode='lines+markers',
            name='Credits Used',
            line=dict(color='#1f77b4', width=2),
            marker=dict(size=6)
        ))
        
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Credits Used",
            hovermode='x unified',
            height=400,
            showlegend=False,
            margin=dict(l=0, r=0, t=30, b=0)
        )
        
        render_plotly_chart(fig)
    
    def _get_snowflake_intelligence_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Snowflake Intelligence usage data from SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY.
        """
        query = """
        SELECT 
            START_TIME,
            END_TIME,
            USER_NAME,
            REQUEST_ID,
            SNOWFLAKE_INTELLIGENCE_NAME,
            AGENT_NAME,
            TOKEN_CREDITS,
            TOKENS
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        try:
            result = self.data_manager.execute_query(query)
            if result is not None and not result.empty:
                if 'START_TIME' in result.columns:
                    result['START_TIME'] = pd.to_datetime(result['START_TIME']).dt.tz_localize(None)
            return result
        except Exception as e:
            return None
    
    def _render_snowflake_intelligence_section(self, data: pd.DataFrame) -> None:
        """
        Render Snowflake Intelligence section with metrics, charts, and data table.
        """
        st.markdown("---")
        st.markdown("#### Snowflake Intelligence")
        st.caption(
            "ⓘ Pricing: Billed in credits based on token usage. Includes Cortex Analyst calls "
            "via Snowflake Intelligence. See Credit Consumption Table 6(f) for rates."
        )
        
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        credit_price = st.session_state.get('credit_price', 2.00)
        
        total_credits = data['TOKEN_CREDITS'].sum()
        total_cost = total_credits * credit_price
        total_tokens = data['TOKENS'].sum()
        request_count = len(data)
        unique_users = data['USER_NAME'].nunique()
        unique_agents = data['AGENT_NAME'].nunique()
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Credits", f"{total_credits:,.2f}")
        with col2:
            st.metric("Total Cost", f"${total_cost:,.2f}")
        with col3:
            st.metric("Requests", f"{request_count:,}")
        with col4:
            st.metric("Unique Agents", f"{unique_agents}")
        
        agent_agg = data.groupby('AGENT_NAME').agg({
            'TOKEN_CREDITS': 'sum',
            'TOKENS': 'sum',
            'REQUEST_ID': 'count'
        }).reset_index()
        agent_agg.columns = ['Agent', 'Credits', 'Tokens', 'Requests']
        agent_agg['Cost'] = agent_agg['Credits'] * credit_price
        agent_agg = agent_agg.sort_values('Cost', ascending=True)
        agent_agg['Agent'] = agent_agg['Agent'].fillna('Unknown')
        agent_agg.loc[agent_agg['Agent'] == '', 'Agent'] = 'Unknown'
        
        st.markdown("##### Cost by Agent")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=agent_agg['Agent'],
            x=agent_agg['Cost'],
            orientation='h',
            marker=dict(color='#9467bd'),
            text=[f"${v:,.2f}" for v in agent_agg['Cost']],
            textposition='auto'
        ))
        
        fig.update_layout(
            xaxis_title="Cost ($)",
            yaxis_title="Agent",
            height=max(300, len(agent_agg) * 40),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        st.markdown("##### Usage Details")
        display_df = agent_agg.copy()
        display_df = display_df.sort_values('Cost', ascending=False)
        display_df['Tokens'] = display_df['Tokens'].apply(lambda x: f"{x:,.0f}")
        display_df['Credits'] = display_df['Credits'].apply(lambda x: f"{x:,.4f}")
        display_df['Cost'] = display_df['Cost'].apply(lambda x: f"${x:,.2f}")
        display_df['Requests'] = display_df['Requests'].apply(lambda x: f"{x:,}")
        
        st.dataframe(
            display_df[['Agent', 'Requests', 'Tokens', 'Credits', 'Cost']],
            use_container_width=True,
            hide_index=True,
            height=min(400, len(display_df) * 35 + 50)
        )
    
    def _get_cortex_agents_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Cortex Agents usage data from CORTEX_AGENT_USAGE_HISTORY.
        """
        query = """
        SELECT 
            START_TIME,
            END_TIME,
            USER_NAME,
            REQUEST_ID,
            AGENT_DATABASE_NAME,
            AGENT_SCHEMA_NAME,
            AGENT_NAME,
            TOKEN_CREDITS,
            TOKENS
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        try:
            result = self.data_manager.execute_query(query)
            if result is not None and not result.empty:
                if 'START_TIME' in result.columns:
                    result['START_TIME'] = pd.to_datetime(result['START_TIME']).dt.tz_localize(None)
            return result
        except Exception as e:
            return None
    
    def _render_cortex_agents_section(self, data: pd.DataFrame) -> None:
        """
        Render Cortex Agents section with metrics, charts, and data table.
        """
        st.markdown("---")
        st.markdown("#### Cortex Agents")
        st.caption(
            "ⓘ Pricing: Billed in credits based on token usage. Includes orchestration and tool calls "
            "(e.g., Cortex Analyst, Cortex Search). See Credit Consumption Table 6(e) for rates."
        )
        
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        credit_price = st.session_state.get('credit_price', 2.00)
        
        total_credits = data['TOKEN_CREDITS'].sum()
        total_cost = total_credits * credit_price
        total_tokens = data['TOKENS'].sum()
        request_count = len(data)
        unique_users = data['USER_NAME'].nunique()
        unique_agents = data['AGENT_NAME'].nunique()
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Credits", f"{total_credits:,.2f}")
        with col2:
            st.metric("Total Cost", f"${total_cost:,.2f}")
        with col3:
            st.metric("Requests", f"{request_count:,}")
        with col4:
            st.metric("Unique Agents", f"{unique_agents}")
        
        data = data.copy()
        data['AGENT_NAME'] = data['AGENT_NAME'].fillna('Unknown')
        data.loc[data['AGENT_NAME'] == '', 'AGENT_NAME'] = 'Unknown'
        
        agent_agg = data.groupby('AGENT_NAME').agg({
            'TOKEN_CREDITS': 'sum',
            'TOKENS': 'sum',
            'REQUEST_ID': 'count'
        }).reset_index()
        agent_agg.columns = ['Agent', 'Credits', 'Tokens', 'Requests']
        agent_agg['Cost'] = agent_agg['Credits'] * credit_price
        agent_agg = agent_agg.sort_values('Cost', ascending=True)
        
        st.markdown("##### Cost by Agent")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=agent_agg['Agent'],
            x=agent_agg['Cost'],
            orientation='h',
            marker=dict(color='#2ca02c'),
            text=[f"${v:,.2f}" for v in agent_agg['Cost']],
            textposition='auto'
        ))
        
        fig.update_layout(
            xaxis_title="Cost ($)",
            yaxis_title="Agent",
            height=max(300, len(agent_agg) * 40),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        st.markdown("##### Usage Details")
        display_df = agent_agg.copy()
        display_df = display_df.sort_values('Cost', ascending=False)
        display_df['Tokens'] = display_df['Tokens'].apply(lambda x: f"{x:,.0f}")
        display_df['Credits'] = display_df['Credits'].apply(lambda x: f"{x:,.4f}")
        display_df['Cost'] = display_df['Cost'].apply(lambda x: f"${x:,.2f}")
        display_df['Requests'] = display_df['Requests'].apply(lambda x: f"{x:,}")
        
        st.dataframe(
            display_df[['Agent', 'Requests', 'Tokens', 'Credits', 'Cost']],
            use_container_width=True,
            hide_index=True,
            height=min(400, len(display_df) * 35 + 50)
        )
    
    def _get_cortex_functions_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Cortex Functions usage data from CORTEX_FUNCTIONS_USAGE_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY to get
        function-level AI usage with details on function names, models, and token credits.
        
        Test Query:
        SELECT 
            FUNCTION_NAME,
            MODEL_NAME,
            START_TIME,
            END_TIME,
            TOKEN_CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns FUNCTION_NAME, MODEL_NAME, 
                                   START_TIME, END_TIME, TOKEN_CREDITS,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            FUNCTION_NAME,
            MODEL_NAME,
            START_TIME,
            END_TIME,
            TOKEN_CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_cortex_functions"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL token_credits (convert to 0)
            if 'TOKEN_CREDITS' in data.columns:
                data['TOKEN_CREDITS'] = data['TOKEN_CREDITS'].fillna(0)
            
            # Handle timezone normalization for timestamp columns
            if 'START_TIME' in data.columns:
                data['START_TIME'] = pd.to_datetime(data['START_TIME']).dt.tz_localize(None)
            
            if 'END_TIME' in data.columns:
                data['END_TIME'] = pd.to_datetime(data['END_TIME']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            # View may not exist in some accounts
            st.warning(f"Cortex Functions data not available: {str(e)}")
            return None
    
    def _render_cortex_functions_section(self, data: pd.DataFrame) -> None:
        """
        Render Cortex Functions section with metrics, charts, and data table.
        
        Displays:
        - Section header with date range
        - Two key metrics: Total Credits, Unique Function Count
        - Horizontal bar chart: Credits by Function Name
        - Horizontal bar chart: Credits by Model Name
        - Sortable data table with aggregated details
        
        Args:
            data: DataFrame with columns FUNCTION_NAME, MODEL_NAME, TOKEN_CREDITS, START_TIME, END_TIME
        """
        # Section header
        st.markdown("---")
        st.markdown("#### AI Functions")
        
        # Display date range
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['TOKEN_CREDITS'].sum()
        unique_functions = data['FUNCTION_NAME'].nunique()
        
        # Display metrics
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Unique Functions", f"{unique_functions}")
        
        # Aggregate by function name and clean up quotes
        func_data = data.copy()
        func_data['FUNCTION_NAME'] = func_data['FUNCTION_NAME'].astype(str).str.replace('"', '')
        function_agg = func_data.groupby('FUNCTION_NAME').agg({
            'TOKEN_CREDITS': 'sum'
        }).reset_index().sort_values('TOKEN_CREDITS', ascending=True)
        
        # Bar chart - Credits by Function Name
        st.markdown("##### Credits by Function Name")
        fig_func = go.Figure()
        fig_func.add_trace(go.Bar(
            y=function_agg['FUNCTION_NAME'],
            x=function_agg['TOKEN_CREDITS'],
            orientation='h',
            marker=dict(color='#1f77b4')
        ))
        
        fig_func.update_layout(
            xaxis_title="Credits",
            yaxis_title="Function Name",
            height=max(300, len(function_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig_func)
        
        # Aggregate by model name - replace nulls/empty with "Various AI Functions"
        model_data = data.copy()
        model_data['MODEL_NAME'] = model_data['MODEL_NAME'].fillna('Various AI Functions')
        # Clean up quotes first, then check for empty
        model_data['MODEL_NAME'] = model_data['MODEL_NAME'].astype(str).str.replace('"', '')
        model_data.loc[model_data['MODEL_NAME'].str.strip() == '', 'MODEL_NAME'] = 'Various AI Functions'
        model_data.loc[model_data['MODEL_NAME'] == 'None', 'MODEL_NAME'] = 'Various AI Functions'
        
        model_agg = model_data.groupby('MODEL_NAME').agg({
            'TOKEN_CREDITS': 'sum'
        }).reset_index().sort_values('TOKEN_CREDITS', ascending=True)
        
        # Bar chart - Credits by Model Name
        st.markdown("##### Credits by Model Name")
        fig_model = go.Figure()
        fig_model.add_trace(go.Bar(
            y=model_agg['MODEL_NAME'],
            x=model_agg['TOKEN_CREDITS'],
            orientation='h',
            marker=dict(color='#2ca02c')
        ))
        
        fig_model.update_layout(
            xaxis_title="Credits",
            yaxis_title="Model Name",
            height=max(300, len(model_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig_model)
        
        # Data table - Aggregated by Function and Model
        st.markdown("##### Function Usage Details")
        
        # Clean up quotes in data for table
        table_data = data.copy()
        table_data['FUNCTION_NAME'] = table_data['FUNCTION_NAME'].astype(str).str.replace('"', '')
        table_data['MODEL_NAME'] = table_data['MODEL_NAME'].fillna('Various AI Functions')
        table_data['MODEL_NAME'] = table_data['MODEL_NAME'].astype(str).str.replace('"', '')
        table_data.loc[table_data['MODEL_NAME'].str.strip() == '', 'MODEL_NAME'] = 'Various AI Functions'
        
        # Aggregate for table
        table_agg = table_data.groupby(['FUNCTION_NAME', 'MODEL_NAME']).agg({
            'TOKEN_CREDITS': 'sum',
            'START_TIME': ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        table_agg.columns = ['Function Name', 'Model Name', 'Total Credits', 'First Usage', 'Last Usage']
        
        # Format columns
        table_agg['Total Credits'] = table_agg['Total Credits'].apply(self._format_credits)
        table_agg['First Usage'] = pd.to_datetime(table_agg['First Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Last Usage'] = pd.to_datetime(table_agg['Last Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Date Range'] = table_agg['First Usage'] + ' to ' + table_agg['Last Usage']
        
        # Add usage count
        usage_counts = table_data.groupby(['FUNCTION_NAME', 'MODEL_NAME']).size().reset_index(name='Usage Count')
        table_agg = table_agg.merge(
            usage_counts,
            left_on=['Function Name', 'Model Name'],
            right_on=['FUNCTION_NAME', 'MODEL_NAME'],
            how='left'
        )
        
        # Display table
        display_cols = ['Function Name', 'Model Name', 'Total Credits', 'Usage Count', 'Date Range']
        st.dataframe(
            table_agg[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400
        )
    
    def _get_cortex_analyst_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Cortex Analyst usage data from CORTEX_ANALYST_USAGE_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.CORTEX_ANALYST_USAGE_HISTORY to get
        analyst-level AI usage with details on usernames, credits, and request counts.
        
        Test Query:
        SELECT 
            USERNAME,
            CREDITS,
            REQUEST_COUNT,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_ANALYST_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns USERNAME, CREDITS, REQUEST_COUNT,
                                   START_TIME, END_TIME,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            USERNAME,
            CREDITS,
            REQUEST_COUNT,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_ANALYST_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_cortex_analyst"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL credits and request_count (convert to 0)
            if 'CREDITS' in data.columns:
                data['CREDITS'] = data['CREDITS'].fillna(0)
            
            if 'REQUEST_COUNT' in data.columns:
                data['REQUEST_COUNT'] = data['REQUEST_COUNT'].fillna(0)
            
            # Handle timezone normalization for timestamp columns
            if 'START_TIME' in data.columns:
                data['START_TIME'] = pd.to_datetime(data['START_TIME']).dt.tz_localize(None)
            
            if 'END_TIME' in data.columns:
                data['END_TIME'] = pd.to_datetime(data['END_TIME']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            # View may not exist in some accounts
            st.warning(f"Cortex Analyst data not available: {str(e)}")
            return None
    
    def _render_cortex_analyst_section(self, data: pd.DataFrame) -> None:
        """
        Render Cortex Analyst section with metrics, chart, and data table.
        
        Displays:
        - Section header with date range
        - Two key metrics: Total Credits, Total Requests
        - Horizontal bar chart: Credits by Username
        - Sortable data table with aggregated details
        
        Args:
            data: DataFrame with columns USERNAME, CREDITS, REQUEST_COUNT, START_TIME, END_TIME
        """
        # Section header
        st.markdown("---")
        st.markdown("#### Cortex Analyst")
        
        # Display date range
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['CREDITS'].sum()
        total_requests = data['REQUEST_COUNT'].sum()
        
        # Display metrics
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Total Requests", f"{int(total_requests):,}")
        
        # Aggregate by username
        user_agg = data.groupby('USERNAME').agg({
            'CREDITS': 'sum',
            'REQUEST_COUNT': 'sum'
        }).reset_index().sort_values('CREDITS', ascending=True)
        
        # Bar chart - Credits by Username
        st.markdown("##### Credits by Username")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=user_agg['USERNAME'],
            x=user_agg['CREDITS'],
            orientation='h',
            marker=dict(color='#ff7f0e')
        ))
        
        fig.update_layout(
            xaxis_title="Credits",
            yaxis_title="Username",
            height=max(300, len(user_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        # Data table - Aggregated by Username
        st.markdown("##### Analyst Usage Details")
        
        # Aggregate for table
        table_agg = data.groupby('USERNAME').agg({
            'CREDITS': 'sum',
            'REQUEST_COUNT': 'sum',
            'START_TIME': ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        table_agg.columns = ['Username', 'Total Credits', 'Request Count', 'First Usage', 'Last Usage']
        
        # Format columns
        table_agg['Total Credits'] = table_agg['Total Credits'].apply(self._format_credits)
        table_agg['Request Count'] = table_agg['Request Count'].astype(int)
        table_agg['First Usage'] = pd.to_datetime(table_agg['First Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Last Usage'] = pd.to_datetime(table_agg['Last Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Date Range'] = table_agg['First Usage'] + ' to ' + table_agg['Last Usage']
        
        # Display table
        display_cols = ['Username', 'Total Credits', 'Request Count', 'Date Range']
        st.dataframe(
            table_agg[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400
        )
    
    def _get_cortex_search_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Cortex Search usage data from CORTEX_SEARCH_DAILY_USAGE_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY to get
        search service usage with details on databases, schemas, services, and consumption types.
        
        Test Query:
        SELECT 
            USAGE_DATE,
            DATABASE_NAME,
            SCHEMA_NAME,
            SERVICE_NAME,
            CONSUMPTION_TYPE,
            CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY
        WHERE USAGE_DATE >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY USAGE_DATE DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns USAGE_DATE, DATABASE_NAME, SCHEMA_NAME,
                                   SERVICE_NAME, CONSUMPTION_TYPE, CREDITS,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            USAGE_DATE,
            DATABASE_NAME,
            SCHEMA_NAME,
            SERVICE_NAME,
            CONSUMPTION_TYPE,
            CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY
        WHERE USAGE_DATE >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY USAGE_DATE DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_cortex_search"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL credits (convert to 0)
            if 'CREDITS' in data.columns:
                data['CREDITS'] = data['CREDITS'].fillna(0)
            
            # Handle timezone normalization for date column
            if 'USAGE_DATE' in data.columns:
                data['USAGE_DATE'] = pd.to_datetime(data['USAGE_DATE']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            # View may not exist in some accounts
            st.warning(f"Cortex Search data not available: {str(e)}")
            return None
    
    def _render_cortex_search_section(self, data: pd.DataFrame) -> None:
        """
        Render Cortex Search section with metrics, charts, and data table.
        
        Displays:
        - Section header with date range
        - Two key metrics: Total Credits, Unique Service Count
        - Horizontal bar chart: Credits by Service Name
        - Horizontal bar chart: Credits by Consumption Type
        - Sortable data table with aggregated details
        
        Args:
            data: DataFrame with columns USAGE_DATE, DATABASE_NAME, SCHEMA_NAME, 
                  SERVICE_NAME, CONSUMPTION_TYPE, CREDITS
        """
        # Section header
        st.markdown("---")
        st.markdown("#### Cortex Search")
        
        # Display date range
        if not data.empty and 'USAGE_DATE' in data.columns:
            min_date = data['USAGE_DATE'].min()
            max_date = data['USAGE_DATE'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['CREDITS'].sum()
        unique_services = data['SERVICE_NAME'].nunique()
        
        # Display metrics
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Unique Services", f"{unique_services}")
        
        # Aggregate by service name
        service_agg = data.groupby('SERVICE_NAME').agg({
            'CREDITS': 'sum'
        }).reset_index().sort_values('CREDITS', ascending=True)
        
        # Bar chart - Credits by Service Name
        st.markdown("##### Credits by Service Name")
        fig_service = go.Figure()
        fig_service.add_trace(go.Bar(
            y=service_agg['SERVICE_NAME'],
            x=service_agg['CREDITS'],
            orientation='h',
            marker=dict(color='#d62728')
        ))
        
        fig_service.update_layout(
            xaxis_title="Credits",
            yaxis_title="Service Name",
            height=max(300, len(service_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig_service)
        
        # Aggregate by consumption type
        type_agg = data.groupby('CONSUMPTION_TYPE').agg({
            'CREDITS': 'sum'
        }).reset_index().sort_values('CREDITS', ascending=True)
        
        # Bar chart - Credits by Consumption Type
        st.markdown("##### Credits by Consumption Type")
        fig_type = go.Figure()
        fig_type.add_trace(go.Bar(
            y=type_agg['CONSUMPTION_TYPE'],
            x=type_agg['CREDITS'],
            orientation='h',
            marker=dict(color='#9467bd')
        ))
        
        fig_type.update_layout(
            xaxis_title="Credits",
            yaxis_title="Consumption Type",
            height=max(300, len(type_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig_type)
        
        # Data table - Aggregated by Database, Schema, Service, and Type
        st.markdown("##### Search Usage Details")
        
        # Aggregate for table
        table_agg = data.groupby(['DATABASE_NAME', 'SCHEMA_NAME', 'SERVICE_NAME', 'CONSUMPTION_TYPE']).agg({
            'CREDITS': 'sum',
            'USAGE_DATE': ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        table_agg.columns = ['Database', 'Schema', 'Service Name', 'Consumption Type', 
                            'Total Credits', 'First Usage', 'Last Usage']
        
        # Format columns
        table_agg['Total Credits'] = table_agg['Total Credits'].apply(self._format_credits)
        table_agg['First Usage'] = pd.to_datetime(table_agg['First Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Last Usage'] = pd.to_datetime(table_agg['Last Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Date Range'] = table_agg['First Usage'] + ' to ' + table_agg['Last Usage']
        
        # Display table
        display_cols = ['Database', 'Schema', 'Service Name', 'Consumption Type', 'Total Credits', 'Date Range']
        st.dataframe(
            table_agg[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400
        )
    
    def _get_document_ai_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Document AI usage data from DOCUMENT_AI_USAGE_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.DOCUMENT_AI_USAGE_HISTORY to get
        document processing usage with details on operations, page counts, and document counts.
        
        Test Query:
        SELECT 
            OPERATION_NAME,
            PAGE_COUNT,
            DOCUMENT_COUNT,
            CREDITS_USED,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.DOCUMENT_AI_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns OPERATION_NAME, PAGE_COUNT, DOCUMENT_COUNT,
                                   CREDITS_USED, START_TIME, END_TIME,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            OPERATION_NAME,
            PAGE_COUNT,
            DOCUMENT_COUNT,
            CREDITS_USED,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.DOCUMENT_AI_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_document_ai"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL values (convert to 0)
            if 'CREDITS_USED' in data.columns:
                data['CREDITS_USED'] = data['CREDITS_USED'].fillna(0)
            
            if 'PAGE_COUNT' in data.columns:
                data['PAGE_COUNT'] = data['PAGE_COUNT'].fillna(0)
            
            if 'DOCUMENT_COUNT' in data.columns:
                data['DOCUMENT_COUNT'] = data['DOCUMENT_COUNT'].fillna(0)
            
            # Handle timezone normalization for timestamp columns
            if 'START_TIME' in data.columns:
                data['START_TIME'] = pd.to_datetime(data['START_TIME']).dt.tz_localize(None)
            
            if 'END_TIME' in data.columns:
                data['END_TIME'] = pd.to_datetime(data['END_TIME']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            # View may not exist in some accounts
            st.warning(f"Document AI data not available: {str(e)}")
            return None
    
    def _render_document_ai_section(self, data: pd.DataFrame) -> None:
        """
        Render Document AI section with metrics, chart, and data table.
        
        Displays:
        - Section header with date range
        - Three key metrics: Total Credits, Total Pages, Total Documents
        - Horizontal bar chart: Credits by Operation Name
        - Sortable data table with aggregated details
        
        Args:
            data: DataFrame with columns OPERATION_NAME, PAGE_COUNT, DOCUMENT_COUNT,
                  CREDITS_USED, START_TIME, END_TIME
        """
        # Section header
        st.markdown("---")
        st.markdown("#### 📄 Document AI")
        
        # Display date range
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['CREDITS_USED'].sum()
        total_pages = data['PAGE_COUNT'].sum()
        total_docs = data['DOCUMENT_COUNT'].sum()
        
        # Display metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Total Pages", f"{int(total_pages):,}")
        with col3:
            st.metric("Total Documents", f"{int(total_docs):,}")
        
        # Aggregate by operation name
        op_agg = data.groupby('OPERATION_NAME').agg({
            'CREDITS_USED': 'sum',
            'PAGE_COUNT': 'sum',
            'DOCUMENT_COUNT': 'sum'
        }).reset_index().sort_values('CREDITS_USED', ascending=True)
        
        # Bar chart - Credits by Operation Name
        st.markdown("##### Credits by Operation Name")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=op_agg['OPERATION_NAME'],
            x=op_agg['CREDITS_USED'],
            orientation='h',
            marker=dict(color='#8c564b')
        ))
        
        fig.update_layout(
            xaxis_title="Credits",
            yaxis_title="Operation Name",
            height=max(300, len(op_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        # Data table - Aggregated by Operation
        st.markdown("##### Document AI Usage Details")
        
        # Aggregate for table
        table_agg = data.groupby('OPERATION_NAME').agg({
            'CREDITS_USED': 'sum',
            'PAGE_COUNT': 'sum',
            'DOCUMENT_COUNT': 'sum',
            'START_TIME': ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        table_agg.columns = ['Operation Name', 'Total Credits', 'Total Pages', 
                            'Total Documents', 'First Usage', 'Last Usage']
        
        # Format columns
        table_agg['Total Credits'] = table_agg['Total Credits'].apply(self._format_credits)
        table_agg['Total Pages'] = table_agg['Total Pages'].astype(int)
        table_agg['Total Documents'] = table_agg['Total Documents'].astype(int)
        table_agg['First Usage'] = pd.to_datetime(table_agg['First Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Last Usage'] = pd.to_datetime(table_agg['Last Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Date Range'] = table_agg['First Usage'] + ' to ' + table_agg['Last Usage']
        
        # Display table
        display_cols = ['Operation Name', 'Total Credits', 'Total Pages', 'Total Documents', 'Date Range']
        st.dataframe(
            table_agg[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400
        )
    
    def _get_fine_tuning_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Fine-Tuning usage data from CORTEX_FINE_TUNING_USAGE_HISTORY.
        
        Queries SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FINE_TUNING_USAGE_HISTORY to get
        model fine-tuning usage with details on models and token credits.
        
        Test Query:
        SELECT 
            MODEL_NAME,
            TOKEN_CREDITS,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FINE_TUNING_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC;
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with columns MODEL_NAME, TOKEN_CREDITS,
                                   START_TIME, END_TIME,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            MODEL_NAME,
            TOKEN_CREDITS,
            START_TIME,
            END_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FINE_TUNING_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        
        try:
            # Use cached query for better performance (1 hour cache)
            cache_key = "ai_services_fine_tuning"
            data = self.data_manager.get_cached_query_result(query, cache_key)
            
            if data is None or data.empty:
                return None
            
            # Handle NULL token_credits (convert to 0)
            if 'TOKEN_CREDITS' in data.columns:
                data['TOKEN_CREDITS'] = data['TOKEN_CREDITS'].fillna(0)
            
            # Handle timezone normalization for timestamp columns
            if 'START_TIME' in data.columns:
                data['START_TIME'] = pd.to_datetime(data['START_TIME']).dt.tz_localize(None)
            
            if 'END_TIME' in data.columns:
                data['END_TIME'] = pd.to_datetime(data['END_TIME']).dt.tz_localize(None)
            
            return data
            
        except Exception as e:
            # View may not exist in some accounts
            st.warning(f"Fine-Tuning data not available: {str(e)}")
            return None
    
    def _render_fine_tuning_section(self, data: pd.DataFrame) -> None:
        """
        Render Fine-Tuning section with metrics, chart, and data table.
        
        Displays:
        - Section header with date range
        - Two key metrics: Total Credits, Unique Model Count
        - Horizontal bar chart: Credits by Model Name
        - Sortable data table with aggregated details
        
        Args:
            data: DataFrame with columns MODEL_NAME, TOKEN_CREDITS, START_TIME, END_TIME
        """
        # Section header
        st.markdown("---")
        st.markdown("#### Fine-Tuning")
        
        # Display date range
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # Calculate metrics
        total_credits = data['TOKEN_CREDITS'].sum()
        unique_models = data['MODEL_NAME'].nunique()
        
        # Display metrics
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Credits", self._format_credits(total_credits))
        with col2:
            st.metric("Unique Models", f"{unique_models}")
        
        # Aggregate by model name
        model_agg = data.groupby('MODEL_NAME').agg({
            'TOKEN_CREDITS': 'sum'
        }).reset_index().sort_values('TOKEN_CREDITS', ascending=True)
        
        # Bar chart - Credits by Model Name
        st.markdown("##### Credits by Model Name")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=model_agg['MODEL_NAME'],
            x=model_agg['TOKEN_CREDITS'],
            orientation='h',
            marker=dict(color='#e377c2')
        ))
        
        fig.update_layout(
            xaxis_title="Credits",
            yaxis_title="Model Name",
            height=max(300, len(model_agg) * 30),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        # Data table - Aggregated by Model
        st.markdown("##### Fine-Tuning Usage Details")
        
        # Aggregate for table
        table_agg = data.groupby('MODEL_NAME').agg({
            'TOKEN_CREDITS': 'sum',
            'START_TIME': ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        table_agg.columns = ['Model Name', 'Total Credits', 'First Usage', 'Last Usage']
        
        # Format columns
        table_agg['Total Credits'] = table_agg['Total Credits'].apply(self._format_credits)
        table_agg['First Usage'] = pd.to_datetime(table_agg['First Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Last Usage'] = pd.to_datetime(table_agg['Last Usage']).dt.strftime('%Y-%m-%d')
        table_agg['Date Range'] = table_agg['First Usage'] + ' to ' + table_agg['Last Usage']
        
        # Add usage count
        usage_counts = data.groupby('MODEL_NAME').size().reset_index(name='Usage Count')
        table_agg = table_agg.merge(
            usage_counts,
            left_on='Model Name',
            right_on='MODEL_NAME',
            how='left'
        )
        
        # Display table
        display_cols = ['Model Name', 'Total Credits', 'Usage Count', 'Date Range']
        st.dataframe(
            table_agg[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400
        )
    
    def _get_cortex_rest_api_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve Cortex REST API usage data from CORTEX_REST_API_USAGE_HISTORY.
        
        The Cortex REST API is billed based on tokens per model, with pricing
        varying by model size. Pricing is in credits per million tokens.
        
        Returns:
            Optional[pd.DataFrame]: DataFrame with REST API usage data,
                                   or None if query fails or no data exists
        """
        query = """
        SELECT 
            START_TIME,
            END_TIME,
            REQUEST_ID,
            MODEL_NAME,
            TOKENS,
            USER_ID,
            INFERENCE_REGION
        FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('month', -12, CURRENT_DATE())
        ORDER BY START_TIME DESC
        """
        try:
            result = self.data_manager.execute_query(query)
            return result
        except Exception as e:
            return None
    
    def _render_cortex_rest_api_section(self, data: pd.DataFrame) -> None:
        """
        Render Cortex REST API section with metrics, charts, and data table.
        
        The REST API is priced per million tokens with different rates per model.
        Pricing reference: Snowflake Credit Consumption Table 6(b).
        
        Args:
            data: DataFrame with columns MODEL_NAME, TOKENS, START_TIME, etc.
        """
        MODEL_PRICING = {
            'mistral-7b': 0.12,
            'gemma-7b': 0.12,
            'llama3-8b': 0.19,
            'llama3.1-8b': 0.19,
            'llama3.2-1b': 0.05,
            'llama3.2-3b': 0.07,
            'mixtral-8x7b': 0.22,
            'llama2-70b-chat': 0.45,
            'llama3-70b': 0.85,
            'llama3.1-70b': 1.21,
            'llama3.3-70b': 1.21,
            'mistral-large': 1.80,
            'mistral-large2': 1.21,
            'reka-flash': 0.45,
            'reka-core': 3.0,
            'jamba-instruct': 0.33,
            'jamba-1.5-mini': 0.07,
            'jamba-1.5-large': 0.45,
            'llama-3.1-405b': 3.0,
            'snowflake-arctic': 0.24,
            'claude-3-5-sonnet': 5.4,
            'claude-4-opus': 12.0,
            'deepseek-r1': 4.0,
            'openai-gpt-4o': 3.0,
            'openai-gpt-4o-mini': 0.32,
        }
        DEFAULT_PRICING = 0.50
        
        st.markdown("---")
        st.markdown("#### Cortex REST API")
        
        if not data.empty and 'START_TIME' in data.columns:
            min_date = data['START_TIME'].min()
            max_date = data['START_TIME'].max()
            st.caption(f"Data from {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        data = data.copy()
        data['MODEL_NAME'] = data['MODEL_NAME'].fillna('unknown')
        data['MODEL_NAME'] = data['MODEL_NAME'].astype(str).str.replace('"', '')
        
        def get_model_price(model_name):
            model_lower = str(model_name).lower()
            for key, price in MODEL_PRICING.items():
                if key in model_lower:
                    return price
            return DEFAULT_PRICING
        
        data['CREDITS_PER_M_TOKENS'] = data['MODEL_NAME'].apply(get_model_price)
        data['CREDITS'] = (data['TOKENS'] / 1_000_000) * data['CREDITS_PER_M_TOKENS']
        
        credit_price = st.session_state.get('credit_price', 2.00)
        
        total_tokens = data['TOKENS'].sum()
        total_credits = data['CREDITS'].sum()
        total_cost = total_credits * credit_price
        unique_models = data['MODEL_NAME'].nunique()
        request_count = len(data)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if total_tokens >= 1_000_000:
                st.metric("Total Tokens", f"{total_tokens/1_000_000:,.2f}M")
            else:
                st.metric("Total Tokens", f"{total_tokens:,.0f}")
        with col2:
            st.metric("Total Credits", f"{total_credits:,.4f}")
        with col3:
            st.metric("Total Cost", f"${total_cost:,.2f}")
        with col4:
            st.metric("API Requests", f"{request_count:,}")
        
        model_agg = data.groupby('MODEL_NAME').agg({
            'TOKENS': 'sum',
            'CREDITS': 'sum',
            'REQUEST_ID': 'count'
        }).reset_index()
        model_agg.columns = ['Model', 'Tokens', 'Credits', 'Requests']
        model_agg['Cost'] = model_agg['Credits'] * credit_price
        model_agg = model_agg.sort_values('Cost', ascending=True)
        
        st.markdown("##### Cost by Model")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=model_agg['Model'],
            x=model_agg['Cost'],
            orientation='h',
            marker=dict(color='#17becf'),
            text=[f"${v:,.2f}" for v in model_agg['Cost']],
            textposition='auto'
        ))
        
        fig.update_layout(
            xaxis_title="Cost ($)",
            yaxis_title="Model",
            height=max(300, len(model_agg) * 40),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0)
        )
        
        render_plotly_chart(fig)
        
        st.markdown("##### REST API Usage Details")
        
        display_df = model_agg.copy()
        display_df = display_df.sort_values('Cost', ascending=False)
        display_df['Tokens'] = display_df['Tokens'].apply(lambda x: f"{x:,.0f}")
        display_df['Credits'] = display_df['Credits'].apply(lambda x: f"{x:,.4f}")
        display_df['Cost'] = display_df['Cost'].apply(lambda x: f"${x:,.2f}")
        display_df['Requests'] = display_df['Requests'].apply(lambda x: f"{x:,}")
        
        st.dataframe(
            display_df[['Model', 'Requests', 'Tokens', 'Credits', 'Cost']],
            use_container_width=True,
            hide_index=True,
            height=min(400, len(display_df) * 35 + 50)
        )
    
    def _format_credits(self, credits: float) -> str:
        """
        Format credit values consistently with dollar amount.
        
        Args:
            credits: Credit value to format
            
        Returns:
            Formatted credit string with dollar amount (e.g., "1,234.56 ($2,469.12)")
        """
        if pd.isna(credits) or credits is None:
            return "0.00 ($0.00)"
        credit_price = st.session_state.get('credit_price', 2.00)
        dollar_amount = credits * credit_price
        if dollar_amount < 1000:
            return f"{credits:,.2f} (${dollar_amount:,.2f})"
        else:
            return f"{credits:,.2f} (${dollar_amount:,.0f})"
    
    def _handle_no_data(self, service_name: str) -> None:
        """
        Display user-friendly message when no data is available for a service.
        
        Args:
            service_name: Name of the service with no data
        """
        st.info(f"No {service_name} usage found in the last 12 months.")


# Removed: ComprehensiveAIServicesAnalyzer class (replaced by AIServicesAnalyzer above)


class DataAccessManager:
    """
    Manages Snowflake database connections and query execution for the cost dashboard.
    Designed to work optimally within Snowflake's Streamlit environment.
    """
    
    def __init__(self):
        """Initialize the data access manager."""
        self.session = None
        self.connection_type = None
        self._connection_validated = False
        self._last_validation_time = None
        self._validation_ttl = 300  # 5 minutes TTL for connection validation
        
        # Query retry configuration
        self.max_retries = 3
        self.retry_delay = 1.0
        
        # Initialize connection
        self._initialize_connection()
    
    def _initialize_connection(self):
        """Initialize connection based on environment (Snowflake Streamlit vs external)."""
        try:
            if SNOWFLAKE_AVAILABLE:
                # Try Snowflake Streamlit environment first
                try:
                    self.session = get_active_session()
                    self.connection_type = "snowpark_session"
                    st.session_state.connection_info = "Connected via Snowflake Streamlit Session"
                    self.session.sql("USE WAREHOUSE AICOLLEGE").collect()
                    st.session_state.connection_validated = True
                    self._connection_validated = True
                    return
                except Exception:
                    pass
            
            # Fallback for local development - create Snowpark session from connection name
            from snowflake.snowpark import Session
            import os
            conn_name = os.getenv("SNOWFLAKE_CONNECTION_NAME") or "jgilstrap_demo"
            self.session = Session.builder.config("connection_name", conn_name).create()
            self.session.sql("USE ROLE SYSADMIN").collect()
            self.session.sql("USE WAREHOUSE AICOLLEGE").collect()
            self.connection_type = "snowpark_session"
            st.session_state.connection_info = "Connected via local Snowpark Session"
            st.session_state.connection_validated = True
            self._connection_validated = True
                
        except Exception as e:
            st.session_state.connection_info = f"Connection initialization failed: {str(e)}"
            self.connection_type = None
    
    def validate_connection(self) -> bool:
        """
        Validate the connection to Snowflake and access to ACCOUNT_USAGE schema.
        
        Returns:
            bool: True if connection is valid and ACCOUNT_USAGE is accessible
        """
        # Check if we need to revalidate based on TTL
        current_time = time.time()
        if (self._connection_validated and self._last_validation_time and 
            (current_time - self._last_validation_time) < self._validation_ttl):
            return True
        
        try:
            # Test basic connection with a simple query
            test_query = "SELECT CURRENT_USER() as CURRENT_USER, CURRENT_ROLE() as CURRENT_ROLE"
            result = self.execute_query(test_query)
            
            if result is None or result.empty:
                self._connection_validated = False
                st.session_state.last_error = "Basic connection test failed"
                return False
            
            # Test ACCOUNT_USAGE schema access with a more specific approach
            try:
                # Test if we can access INFORMATION_SCHEMA first
                info_schema_test = """
                SELECT COUNT(*) as table_count 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = 'ACCOUNT_USAGE' 
                AND TABLE_NAME = 'METERING_HISTORY'
                """
                
                schema_result = self.execute_query(info_schema_test)
                
                if schema_result is None or schema_result.empty:
                    self._connection_validated = False
                    st.session_state.last_error = "Cannot access INFORMATION_SCHEMA or ACCOUNT_USAGE schema not visible"
                    return False
                
                table_count = schema_result.iloc[0]['TABLE_COUNT']
                if table_count == 0:
                    self._connection_validated = False
                    st.session_state.last_error = "METERING_HISTORY table not found in ACCOUNT_USAGE schema"
                    return False
                
                # Now test actual data access
                data_test = "SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY LIMIT 1"
                data_result = self.execute_query(data_test)
                
                if data_result is None:
                    self._connection_validated = False
                    st.session_state.last_error = "Cannot query METERING_HISTORY - insufficient permissions"
                    return False
                
                # If we get here, validation passed
                self._connection_validated = True
                self._last_validation_time = current_time
                st.session_state.last_error = None
                return True
                
            except Exception as access_error:
                self._connection_validated = False
                st.session_state.last_error = f"ACCOUNT_USAGE access test failed: {str(access_error)}"
                return False
            
        except Exception as e:
            st.session_state.last_error = f"Connection validation failed: {str(e)}"
            self._connection_validated = False
            return False
    
    def execute_query(self, query: str, params: Optional[Dict] = None) -> Optional[pd.DataFrame]:
        """
        Execute a SQL query with error handling and retry logic.
        
        Args:
            query (str): SQL query to execute
            params (Optional[Dict]): Query parameters for parameterized queries
            
        Returns:
            Optional[pd.DataFrame]: Query results as DataFrame, None if query fails
        """
        if not self.session and self.connection_type != "external_connector":
            st.error("❌ No active Snowflake session available")
            return None
        
        # Execute query with retry logic
        for attempt in range(self.max_retries):
            try:
                if self.connection_type == "snowpark_session":
                    # Use Snowpark session (preferred for Snowflake Streamlit)
                    result = self.session.sql(query).to_pandas()
                    return result
                    
                elif self.connection_type == "external_connector" and hasattr(self, '_external_conn'):
                    # Fallback for external connections (development only)
                    cursor = self._external_conn.cursor()
                    cursor.execute(query)
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    data = cursor.fetchall()
                    cursor.close()
                    return pd.DataFrame(data, columns=columns)
                else:
                    st.error("❌ No valid connection available")
                    return None
                    
            except Exception as e:
                error_msg = str(e)
                
                # Check for specific error types
                if "does not exist" in error_msg.lower():
                    st.error(f"❌ Database object not found: {error_msg}")
                    return None
                elif "access denied" in error_msg.lower() or "permission" in error_msg.lower():
                    st.error(f"❌ Access denied. Please check permissions for ACCOUNT_USAGE schema: {error_msg}")
                    return None
                elif attempt < self.max_retries - 1:
                    # Retry for transient errors
                    st.warning(f"Query attempt {attempt + 1} failed, retrying... ({error_msg})")
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                else:
                    # Final attempt failed
                    st.error(f"❌ Query failed after {self.max_retries} attempts: {error_msg}")
                    st.session_state.last_error = error_msg
                    return None
        
        return None
    
    @st.cache_data(ttl=3600)  # Cache for 1 hour
    def get_cached_query_result(_self, query: str, cache_key: str) -> Optional[pd.DataFrame]:
        """
        Execute query with caching for performance optimization.
        
        Args:
            query (str): SQL query to execute
            cache_key (str): Unique key for caching
            
        Returns:
            Optional[pd.DataFrame]: Cached or fresh query results
        """
        return _self.execute_query(query)
    
    def get_account_usage_summary(self) -> Dict[str, any]:
        """
        Get summary information about ACCOUNT_USAGE schema availability and data freshness.
        
        Returns:
            Dict: Summary information about account usage data
        """
        if not self.validate_connection():
            return {
                "status": "error",
                "message": "Connection validation failed",
                "data_available": False
            }
        
        try:
            # Check data availability and freshness
            summary_query = """
            SELECT 
                'METERING_HISTORY' as view_name,
                MAX(START_TIME) as latest_date,
                COUNT(*) as record_count
            FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY
            WHERE START_TIME >= DATEADD(day, -7, CURRENT_DATE())
            
            UNION ALL
            
            SELECT 
                'WAREHOUSE_METERING_HISTORY' as view_name,
                MAX(START_TIME::DATE) as latest_date,
                COUNT(*) as record_count
            FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
            WHERE START_TIME >= DATEADD(day, -7, CURRENT_DATE())
            
            UNION ALL
            
            SELECT 
                'QUERY_HISTORY' as view_name,
                MAX(START_TIME::DATE) as latest_date,
                COUNT(*) as record_count
            FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
            WHERE START_TIME >= DATEADD(day, -7, CURRENT_DATE())
            """
            
            summary_df = self.execute_query(summary_query)
            
            if summary_df is not None and not summary_df.empty:
                return {
                    "status": "success",
                    "message": "Account usage data available",
                    "data_available": True,
                    "view_summary": summary_df.to_dict('records'),
                    "last_updated": datetime.now().isoformat()
                }
            else:
                return {
                    "status": "warning", 
                    "message": "No recent account usage data found",
                    "data_available": False
                }
                
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to retrieve account usage summary: {str(e)}",
                "data_available": False
            }
    
    def test_account_usage_access(self) -> Dict[str, bool]:
        """
        Test access to all required ACCOUNT_USAGE views.
        
        Returns:
            Dict[str, bool]: Access status for each required view
        """
        required_views = [
            "METERING_HISTORY",
            "WAREHOUSE_METERING_HISTORY", 
            "QUERY_HISTORY",
            "STORAGE_USAGE",
            "AUTOMATIC_CLUSTERING_HISTORY"
        ]
        
        access_status = {}
        
        for view_name in required_views:
            try:
                test_query = f"SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.{view_name} LIMIT 1"
                result = self.execute_query(test_query)
                access_status[view_name] = result is not None
                
            except Exception as e:
                access_status[view_name] = False
                if "does not exist" not in str(e).lower():
                    # Log unexpected errors
                    st.session_state.last_error = f"Error testing {view_name}: {str(e)}"
        
        return access_status
    
    def get_connection_info(self) -> Dict[str, any]:
        """
        Get current connection information and status.
        
        Returns:
            Dict: Connection information and status
        """
        return {
            "connection_type": self.connection_type,
            "validated": self._connection_validated,
            "last_validation": self._last_validation_time,
            "session_available": self.session is not None,
            "info": st.session_state.get("connection_info", "No connection info available")
        }

# Configure Streamlit page settings
st.set_page_config(
    page_title="Snowflake Cost Dashboard",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded"
)

class SnowflakeUsageDashboard:
    """Main application controller for the Snowflake usage dashboard."""
    
    def __init__(self):
        """Initialize the dashboard application."""
        self.tabs = [
            "Overview",
            "Storage", 
            "Warehouse Compute",
            "Cloud Services",
            "Replication", 
            "Clustering",
            "Serverless",
            "AI Services",
            "Consumption by Client"
        ]
        
        # Initialize data access manager
        self.data_manager = None
        self.initialize_session_state()
        self.initialize_data_manager()
    
    def initialize_session_state(self):
        """Initialize Streamlit session state variables for navigation and caching."""
        # Navigation state
        if 'current_tab' not in st.session_state:
            st.session_state.current_tab = 'Overview'
        
        # Data caching state
        if 'data_cache' not in st.session_state:
            st.session_state.data_cache = {}
        
        # Cache timestamps for TTL management
        if 'cache_timestamps' not in st.session_state:
            st.session_state.cache_timestamps = {}
        
        # Connection state
        if 'connection_validated' not in st.session_state:
            st.session_state.connection_validated = False
        
        # Error state tracking
        if 'last_error' not in st.session_state:
            st.session_state.last_error = None
        
        # Connection info
        if 'connection_info' not in st.session_state:
            st.session_state.connection_info = "Initializing connection..."
    
    def initialize_data_manager(self):
        """Initialize the data access manager."""
        try:
            if 'data_manager' not in st.session_state:
                st.session_state.data_manager = DataAccessManager()
            
            self.data_manager = st.session_state.data_manager
            
        except Exception as e:
            st.session_state.last_error = f"Failed to initialize data manager: {str(e)}"
            st.session_state.connection_info = f"Data manager initialization failed: {str(e)}"
    
    def render_sidebar(self):
        """Create and render the sidebar navigation."""
        st.markdown("""
            <style>
            /* Snowflake Theme - #00A1D9 blue and white */
            
            /* Main header styling */
            h1, h2, h3 {
                color: #00A1D9 !important;
            }
            
            /* Sidebar styling */
            [data-testid="stSidebar"] {
                background-color: #00A1D9 !important;
            }
            [data-testid="stSidebar"] * {
                color: white !important;
            }
            [data-testid="stSidebar"] h1, 
            [data-testid="stSidebar"] h2, 
            [data-testid="stSidebar"] h3 {
                color: white !important;
            }
            [data-testid="stSidebar"] a {
                color: white !important;
            }
            [data-testid="stSidebar"] .stNumberInput input {
                color: #333 !important;
                background-color: white !important;
            }
            
            /* Primary buttons */
            .stButton > button[kind="primary"] {
                background-color: #00A1D9 !important;
                border-color: #00A1D9 !important;
            }
            
            /* Metric styling */
            [data-testid="stMetricValue"] {
                color: #00A1D9 !important;
            }
            
            /* Tab styling */
            .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
                background-color: #00A1D9 !important;
                color: white !important;
                box-shadow: 0 6px 12px rgba(0, 161, 217, 0.5), 0 3px 6px rgba(0, 0, 0, 0.15) !important;
                transform: translateY(-2px);
                border-bottom: 3px solid white !important;
            }
            .stTabs [data-baseweb="tab-list"] button {
                transition: all 0.2s ease !important;
                border-radius: 4px 4px 0 0 !important;
                color: #00A1D9 !important;
                border-bottom: 3px solid transparent !important;
            }
            .stTabs [data-baseweb="tab-list"] button:hover:not([aria-selected="true"]) {
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1) !important;
                border-bottom: 3px solid #00A1D9 !important;
            }
            
            /* Global hover/selection highlight - blue instead of red */
            ::selection {
                background-color: rgba(0, 161, 217, 0.3) !important;
                color: inherit !important;
            }
            *:focus {
                outline-color: #00A1D9 !important;
            }
            button:hover, [role="button"]:hover {
                border-color: #00A1D9 !important;
            }
            [data-baseweb="select"] [aria-selected="true"],
            [data-baseweb="menu"] li:hover,
            [data-baseweb="popover"] li:hover {
                background-color: rgba(0, 161, 217, 0.1) !important;
            }
            
            /* Multiselect tags/pills - blue instead of red */
            [data-baseweb="tag"] {
                background-color: #00A1D9 !important;
            }
            [data-baseweb="tag"] span {
                color: white !important;
            }
            [data-baseweb="tag"] svg {
                fill: white !important;
            }
            
            /* Sub-headers - blue */
            h2, h3, h4, h6 {
                color: #00A1D9 !important;
            }
            
            /* Links */
            a {
                color: #00A1D9 !important;
            }
            
            /* Dividers */
            hr {
                border-color: #00A1D9 !important;
                opacity: 0.3;
            }
            
            /* Radio buttons - all (blue border, white bg, blue fill when selected) */
            [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {
                border: 2px solid #00A1D9 !important;
                background-color: white !important;
                width: 18px !important;
                height: 18px !important;
            }
            [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child > div {
                background-color: transparent !important;
            }
            [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) > div:first-child > div {
                background-color: #00A1D9 !important;
            }
            
            /* Radio button text in sidebar should be white */
            [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] span {
                color: white !important;
            }
            
            /* Sidebar buttons - dark text on white background */
            [data-testid="stSidebar"] button[kind="secondary"] {
                background-color: white !important;
                color: #333333 !important;
                border: 1px solid #00A1D9 !important;
            }
            [data-testid="stSidebar"] button[kind="secondary"] p {
                color: #333333 !important;
            }
            [data-testid="stSidebar"] button[kind="secondary"]:hover {
                background-color: #00A1D9 !important;
                color: white !important;
            }
            [data-testid="stSidebar"] button[kind="secondary"]:hover p {
                color: white !important;
            }
            </style>
        """, unsafe_allow_html=True)
        
        with st.sidebar:
            st.title("Snowflake Cost Dashboard")
            st.caption("by Jack Gilstrap")
            st.markdown("[Credit Consumption Table](https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf)")
            st.markdown("---")
            
            st.subheader("Settings")
            st.markdown("""
                <style>
                    div[data-testid="stNumberInput"] button {
                        display: none;
                    }
                </style>
            """, unsafe_allow_html=True)
            # Get persisted credit price from URL params on first load only
            if 'credit_price' not in st.session_state:
                if 'credit_price' in st.query_params:
                    try:
                        st.session_state.credit_price = float(st.query_params['credit_price'])
                    except ValueError:
                        st.session_state.credit_price = 2.00
                else:
                    st.session_state.credit_price = 2.00
            
            def on_credit_price_change():
                st.session_state.credit_price = st.session_state.credit_price_input
                st.query_params['credit_price'] = str(st.session_state.credit_price_input)
            
            st.number_input(
                "Credit Price ($)",
                min_value=0.00,
                max_value=100.00,
                value=st.session_state.credit_price,
                step=0.01,
                format="%.2f",
                key="credit_price_input",
                help="Enter the price per Snowflake credit for cost calculations",
                on_change=on_credit_price_change
            )
            st.markdown("---")
            
            # Navigation tabs - use a more reliable approach
            selected_tab = st.radio(
                "Navigate to:",
                self.tabs,
                key="nav_radio"
            )
            
            # Update session state immediately when tab changes
            st.session_state.current_tab = selected_tab
            
            st.markdown("---")
            
            # Connection status and management
            self.render_connection_section()
            
            # Data management controls
            self.render_data_management_section()
    
    def render_connection_section(self):
        """Display connection status and management in sidebar."""
        st.subheader("Connection Status")
        
        if self.data_manager:
            # Get connection info
            conn_info = self.data_manager.get_connection_info()
            
            # Display connection status
            if conn_info.get("session_available", False):
                st.caption(f"Connected ({conn_info.get('connection_type', 'Unknown')})")
            else:
                st.error("❌ Connection not available")
                st.caption(conn_info.get("info", "No connection info"))
            
            # Show expandable connection details for advanced users
            with st.expander("Advanced Connection Info"):
                st.caption("**Connection Details:**")
                st.text(conn_info.get("info", "No connection info available"))
                
                # Manual connection testing buttons (for troubleshooting)
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Test Query", key="test_query_btn", use_container_width=True):
                        self.test_simple_query()
                with col2:
                    if st.button("Check Role", key="check_role_btn", use_container_width=True):
                        self.show_current_role()
        else:
            st.error("❌ Data Manager unavailable")
            if st.button("Reinitialize", key="reinit_btn"):
                self.initialize_data_manager()
    
    def render_data_management_section(self):
        """Display data management controls in sidebar."""
        st.subheader("Data Management")
        
        # Cache management
        cache_count = len(st.session_state.data_cache)
        st.caption(f"Cached queries: {cache_count}")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear Cache", key="clear_cache_btn", use_container_width=True):
                self.clear_cache()
        with col2:
            if st.button("Refresh Data", key="refresh_data_btn", use_container_width=True):
                self.refresh_current_tab_data()
    
    def test_simple_query(self):
        """Test a simple query to verify connection."""
        if not self.data_manager:
            st.error("❌ Data manager not available")
            return
        
        with st.spinner("Testing query execution..."):
            try:
                result = self.data_manager.execute_query("SELECT CURRENT_TIMESTAMP() as NOW")
                if result is not None and not result.empty:
                    st.success("Query test successful!")
                    st.write(f"Current time: {result.iloc[0]['NOW']}")
                else:
                    st.error("❌ Query test failed - no results")
            except Exception as e:
                st.error(f"❌ Query test failed: {str(e)}")
    
    def show_current_role(self):
        """Show current role and user information."""
        if not self.data_manager:
            st.error("❌ Data manager not available")
            return
        
        with st.spinner("Retrieving role information..."):
            try:
                result = self.data_manager.execute_query("""
                    SELECT 
                        CURRENT_USER() as CURRENT_USER,
                        CURRENT_ROLE() as CURRENT_ROLE,
                        CURRENT_WAREHOUSE() as CURRENT_WAREHOUSE
                """)
                if result is not None and not result.empty:
                    st.success("Role information retrieved!")
                    row = result.iloc[0]
                    st.write(f"**User:** {row['CURRENT_USER']}")
                    st.write(f"**Role:** {row['CURRENT_ROLE']}")  
                    st.write(f"**Warehouse:** {row['CURRENT_WAREHOUSE']}")
                else:
                    st.error("❌ Could not retrieve role information")
            except Exception as e:
                st.error(f"❌ Role check failed: {str(e)}")
    
    def show_account_usage_summary(self):
        """Display account usage data summary."""
        if not self.data_manager:
            st.error("❌ Data manager not available")
            return
        
        with st.spinner("Loading account usage summary..."):
            summary = self.data_manager.get_account_usage_summary()
            
            if summary.get("status") == "success":
                st.success("Account usage data is available!")
                
                # Display view summary
                if summary.get("view_summary"):
                    st.markdown("**Data Freshness:**")
                    for view_info in summary["view_summary"]:
                        st.write(f"• **{view_info['VIEW_NAME']}**: {view_info['RECORD_COUNT']} records, latest: {view_info['LATEST_DATE']}")
                        
            elif summary.get("status") == "warning":
                st.warning(f"{summary['message']}")
            else:
                st.error(f"❌ {summary['message']}")
    
    def clear_cache(self):
        """Clear all cached data."""
        st.session_state.data_cache.clear()
        st.session_state.cache_timestamps.clear()
        st.success("🗑Cache cleared successfully!")
        st.rerun()
    
    def refresh_current_tab_data(self):
        """Refresh data for the currently selected tab."""
        current_tab = st.session_state.current_tab
        # Remove current tab data from cache to force refresh
        cache_keys_to_remove = [k for k in st.session_state.data_cache.keys() 
                               if current_tab.lower().replace(" ", "_") in k]
        
        for key in cache_keys_to_remove:
            if key in st.session_state.data_cache:
                del st.session_state.data_cache[key]
            if key in st.session_state.cache_timestamps:
                del st.session_state.cache_timestamps[key]
        
        st.success(f"Refreshed {current_tab} data!")
        st.rerun()
    
    def render_main_content(self):
        """Render the main content area based on selected tab."""
        current_tab = st.session_state.current_tab
        
        # Main content header
        st.title(f"{current_tab}")
        
        # Route to appropriate tab content
        if current_tab == "Overview":
            self.render_overview_tab()
        elif current_tab == "Storage":
            self.render_storage_tab()
        elif current_tab == "Warehouse Compute":
            self.render_consumption_tab()
        elif current_tab == "Cloud Services":
            self.render_cloud_services_tab()
        elif current_tab == "Replication":
            self.render_replication_tab()
        elif current_tab == "Clustering":
            self.render_clustering_tab()
        elif current_tab == "Serverless":
            self.render_serverless_tab()
        elif current_tab == "AI Services":
            self.render_ai_services_tab()
        elif current_tab == "Consumption by Client":
            self.render_client_consumption_tab()
    
    def render_overview_tab(self):
        """Render the Overview dashboard tab."""
        
        # Simple connection check - if we have a session, we're good to go
        if not self.data_manager or not self.data_manager.session:
            st.error("❌ No active Snowflake session available")
            st.info("**Note:** This app requires an active Snowflake Streamlit session.")
            return
        
        # Connection is available - render the dashboard
        
        # Render yearly consumption projection section
        self.render_yearly_projection_section()
        
        # Render monthly service costs section
        st.markdown("---")
        self.render_monthly_service_costs()
    
    def render_yearly_projection_section(self):
        """Render the yearly consumption projection section."""
        st.markdown("### Yearly Consumption Projections")
        st.markdown("Forecast your annual Snowflake costs based on recent usage patterns.")
        
        # Load yearly projection data
        projection_data = self.get_yearly_projection_data()
        
        if projection_data is None or projection_data.empty:
            st.warning("Insufficient data for yearly projections.")
            st.info("**Tip:** Projections require at least 30 days of recent usage data.")
            return
        
        # Projection controls
        col1, col2 = st.columns([3, 1])
        
        with col2:
            st.markdown("#### Projection Settings")
            run_rate_period = st.radio(
                "Run Rate Period:",
                ["30-day", "60-day", "90-day"],
                index=0,
                key="projection_period",
                help="Select the period for calculating average daily consumption"
            )
            
            # Show projection details
            period_days = int(run_rate_period.split('-')[0])
            current_date = datetime.now()
            
            # Calculate projection metrics
            projection_metrics = self.calculate_projection_metrics(projection_data, period_days)
            
            if projection_metrics:
                st.markdown("**Projection Details:**")
                st.metric(
                    label="Current YTD Total",
                    value=format_credits_with_dollars(projection_metrics['ytd_actual'])
                )
                st.metric(
                    label=f"Daily Avg ({period_days} days)",
                    value=format_credits_with_dollars(projection_metrics['daily_average']),
                    help=f"Based on {projection_metrics['actual_days_used']} days of recent data"
                )
                st.metric(
                    label="Projected Year Total",
                    value=format_credits_with_dollars(projection_metrics['projected_total'])
                )
                
                # Show projection date range and actual period used
                st.caption(f"**Actual period used:** {projection_metrics['start_date']} to {projection_metrics['end_date']} ({projection_metrics['actual_days_used']} days)")
        
        with col1:
            # Render projection chart
            self.render_projection_chart(projection_data, period_days, projection_metrics)
    
    def get_yearly_projection_data(self) -> Optional[pd.DataFrame]:
        """
        Retrieve daily consumption data for current year to calculate projections.
        
        Returns:
            Optional[pd.DataFrame]: Daily consumption data for projection calculations
        """
        cache_key = "yearly_projection_data"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            return st.session_state.data_cache[cache_key]
        
        query = """
        SELECT 
            DATE_TRUNC('day', START_TIME) as USAGE_DATE,
            SUM(CREDITS_USED) as DAILY_CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY
        WHERE START_TIME >= DATE_TRUNC('year', CURRENT_DATE())
        GROUP BY DATE_TRUNC('day', START_TIME)
        ORDER BY USAGE_DATE
        """
        
        try:
            with st.spinner("Loading yearly projection data..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load projection data: {str(e)}")
            return None
    
    def calculate_projection_metrics(self, data: pd.DataFrame, period_days: int) -> Optional[Dict]:
        """
        Calculate projection metrics based on recent usage patterns.
        
        Args:
            data (pd.DataFrame): Daily usage data
            period_days (int): Number of days to use for run rate calculation
            
        Returns:
            Optional[Dict]: Projection metrics or None if insufficient data
        """
        if data.empty:
            return None
        
        # Ensure USAGE_DATE column is in datetime format and handle timezone compatibility
        if not pd.api.types.is_datetime64_any_dtype(data['USAGE_DATE']):
            data['USAGE_DATE'] = pd.to_datetime(data['USAGE_DATE'])
        
        # Get current date in the same timezone as the data, or make both timezone-naive
        if data['USAGE_DATE'].dt.tz is not None:
            # Data has timezone info - use the same timezone for current_date
            current_date = pd.Timestamp.now(tz=data['USAGE_DATE'].dt.tz.zone)
        else:
            # Data is timezone-naive - use timezone-naive current_date
            current_date = pd.Timestamp.now().tz_localize(None)
        
        # Create year boundaries in same timezone context
        year_start = pd.Timestamp(current_date.year, 1, 1)
        if current_date.tz is not None:
            year_start = year_start.tz_localize(current_date.tz)
        year_end = pd.Timestamp(current_date.year, 12, 31)
        if current_date.tz is not None:
            year_end = year_end.tz_localize(current_date.tz)
        
        # Calculate YTD actual consumption
        ytd_actual = data['DAILY_CREDITS'].sum()
        
        # Get recent data for run rate calculation - ensure compatible timezone
        recent_cutoff = current_date - pd.Timedelta(days=period_days)
        
        # Filter data for the specified period
        recent_data = data[data['USAGE_DATE'] >= recent_cutoff].copy()
        
        if recent_data.empty:
            return None
        
        # Sort by date to ensure proper calculation
        recent_data = recent_data.sort_values('USAGE_DATE')
        
        # Calculate daily average from recent period
        daily_average = recent_data['DAILY_CREDITS'].mean()
        actual_days_used = len(recent_data)
        
        # Get the actual date range used
        actual_start_date = recent_data['USAGE_DATE'].min()
        actual_end_date = recent_data['USAGE_DATE'].max()
        
        # Calculate days remaining in year - use total_seconds for compatibility
        days_remaining = (year_end - current_date).total_seconds() / (24 * 3600)
        days_remaining = int(days_remaining)
        
        # Calculate projected total
        projected_remaining = daily_average * days_remaining
        projected_total = ytd_actual + projected_remaining
        
        # Convert to string format, handling timezone
        if hasattr(actual_start_date, 'strftime'):
            start_date_str = actual_start_date.strftime('%Y-%m-%d')
            end_date_str = actual_end_date.strftime('%Y-%m-%d')
        else:
            start_date_str = str(actual_start_date)[:10]
            end_date_str = str(actual_end_date)[:10]
        
        return {
            'ytd_actual': ytd_actual,
            'daily_average': daily_average,
            'projected_total': projected_total,
            'projection_increase': projected_remaining,
            'days_remaining': days_remaining,
            'period_days': period_days,  # Requested period
            'actual_days_used': actual_days_used,  # Actual days found in data
            'start_date': start_date_str,
            'end_date': end_date_str,
            'recent_cutoff_date': recent_cutoff.strftime('%Y-%m-%d') if hasattr(recent_cutoff, 'strftime') else str(recent_cutoff)[:10]
        }
    
    def render_projection_chart(self, data: pd.DataFrame, period_days: int, metrics: Dict):
        """
        Render the yearly projection chart showing actual vs projected consumption.
        
        Args:
            data (pd.DataFrame): Daily usage data
            period_days (int): Run rate period in days
            metrics (Dict): Calculated projection metrics
        """
        if not metrics:
            st.warning("Unable to calculate projections with current data")
            return
        
        # Prepare data for visualization - match timezone with data
        if data['USAGE_DATE'].dt.tz is not None:
            # Data has timezone - use same timezone
            current_date = pd.Timestamp.now(tz=data['USAGE_DATE'].dt.tz.zone)
            year_end = pd.Timestamp(current_date.year, 12, 31).tz_localize(current_date.tz)
        else:
            # Data is timezone-naive
            current_date = pd.Timestamp.now().tz_localize(None)
            year_end = pd.Timestamp(current_date.year, 12, 31)
        
        # Create cumulative actual data
        data_sorted = data.copy().sort_values('USAGE_DATE')
        data_sorted['CUMULATIVE_CREDITS'] = data_sorted['DAILY_CREDITS'].cumsum()
        
        # Create projection line from current date to year end
        projection_dates = pd.date_range(current_date, year_end, freq='D')
        ytd_total = metrics['ytd_actual']
        daily_avg = metrics['daily_average']
        
        projection_values = []
        for i, date in enumerate(projection_dates):
            projected_value = ytd_total + (daily_avg * i)
            projection_values.append(projected_value)
        
        projection_df = pd.DataFrame({
            'USAGE_DATE': projection_dates,
            'PROJECTED_CREDITS': projection_values
        })
        
        # Create the chart
        fig = go.Figure()
        
        # Add actual cumulative consumption line
        fig.add_trace(go.Scatter(
            x=data_sorted['USAGE_DATE'],
            y=data_sorted['CUMULATIVE_CREDITS'],
            mode='lines',
            name='Actual YTD Consumption',
            line=dict(color='#1f77b4', width=3),
            hovertemplate='<b>%{fullData.name}</b><br>Date: %{x}<br>Credits: %{y:,.0f}<extra></extra>'
        ))
        
        # Add projection line
        fig.add_trace(go.Scatter(
            x=projection_df['USAGE_DATE'],
            y=projection_df['PROJECTED_CREDITS'],
            mode='lines',
            name=f'Projected ({period_days}-day run rate)',
            line=dict(color='#ff7f0e', width=3, dash='dash'),
            hovertemplate='<b>%{fullData.name}</b><br>Date: %{x}<br>Projected Credits: %{y:,.0f}<extra></extra>'
        ))
        
        # Add current date marker
        current_ytd = data_sorted['CUMULATIVE_CREDITS'].iloc[-1] if not data_sorted.empty else 0
        fig.add_trace(go.Scatter(
            x=[current_date],
            y=[current_ytd],
            mode='markers',
            name='Current Position',
            marker=dict(color='red', size=10, symbol='diamond'),
            hovertemplate='<b>Today</b><br>YTD Credits: %{y:,.0f}<extra></extra>'
        ))
        
        # Update layout
        fig.update_layout(
            title=f'Yearly Consumption Projection ({current_date.year})',
            xaxis_title='Date',
            yaxis_title='Cumulative Credits',
            height=500,
            hovermode='x unified',
            legend=dict(
                orientation="v",
                yanchor="top",
                y=1,
                xanchor="left",
                x=1.02
            ),
            annotations=[
                dict(
                    x=year_end,
                    y=metrics['projected_total'],
                    text=f"Projected Year-End<br>{metrics['projected_total']:,.0f} credits",
                    showarrow=True,
                    arrowhead=2,
                    arrowcolor='#ff7f0e',
                    bgcolor='rgba(255,255,255,0.8)',
                    bordercolor='#ff7f0e'
                )
            ]
        )
        
        render_plotly_chart(fig)
    
    def render_monthly_service_costs(self):
        """Render the monthly service costs section with charts and tables."""
        st.markdown("### Monthly Compute Service Costs & Trends")
        
        # Day range selector
        day_options = {30: "Last 30 Days", 60: "Last 60 Days", 90: "Last 90 Days"}
        selected_days = st.selectbox(
            "Time Period",
            options=list(day_options.keys()),
            format_func=lambda x: day_options[x],
            key="service_costs_days"
        )
        
        # Load monthly service cost data
        monthly_data = self.get_monthly_service_costs(days=selected_days)
        
        if monthly_data is None or monthly_data.empty:
            st.warning("No service cost data available for the selected period.")
            st.info("**Tip:** Ensure your account has recent usage data and proper ACCOUNT_USAGE permissions.")
            return
        
        # Display summary metrics
        self.render_cost_summary_metrics(monthly_data, selected_days)
        
        # Create tabs for different visualizations
        viz_tabs = st.tabs(["Monthly Trends", "Service Breakdown", "Detailed Data"])
        
        with viz_tabs[0]:
            self.render_monthly_trends_chart(monthly_data)
        
        with viz_tabs[1]:
            self.render_service_breakdown_charts(monthly_data)
        
        with viz_tabs[2]:
            self.render_detailed_data_table(monthly_data)
    
    def get_monthly_service_costs(self, days: int = 30) -> Optional[pd.DataFrame]:
        """
        Retrieve daily service costs data for period-over-period comparison.
        
        Args:
            days: Number of days for the period (30, 60, or 90)
        
        Returns:
            Optional[pd.DataFrame]: Daily service costs with period comparison data
        """
        cache_key = f"monthly_service_costs_{days}"
        
        # Check cache first
        if cache_key in st.session_state.data_cache:
            return st.session_state.data_cache[cache_key]
        
        # Query gets data from 24 hours ago going back 2x the period (for current + previous period)
        # End date is 24 hours ago to account for ACCOUNT_USAGE latency
        query = f"""
        WITH period_data AS (
            SELECT 
                DATE(START_TIME) as USAGE_DATE,
                SERVICE_TYPE,
                SUM(CREDITS_USED_COMPUTE) as COMPUTE_CREDITS,
                SUM(CREDITS_USED_CLOUD_SERVICES) as CLOUD_SERVICES_CREDITS,
                SUM(CREDITS_USED) as TOTAL_CREDITS,
                -- Mark which period: 'CURRENT' = last N days, 'PREVIOUS' = N days before that
                CASE 
                    WHEN DATE(START_TIME) > DATEADD('day', -{days}, DATEADD('hour', -24, CURRENT_TIMESTAMP()))
                    THEN 'CURRENT'
                    ELSE 'PREVIOUS'
                END as PERIOD
            FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY
            WHERE START_TIME >= DATEADD('day', -{days * 2}, DATEADD('hour', -24, CURRENT_TIMESTAMP()))
              AND START_TIME < DATEADD('hour', -24, CURRENT_TIMESTAMP())
            GROUP BY DATE(START_TIME), SERVICE_TYPE
        )
        SELECT 
            USAGE_DATE,
            SERVICE_TYPE,
            COMPUTE_CREDITS,
            CLOUD_SERVICES_CREDITS,
            TOTAL_CREDITS,
            PERIOD
        FROM period_data
        ORDER BY USAGE_DATE DESC, SERVICE_TYPE
        """
        
        try:
            with st.spinner("Loading service costs..."):
                result = self.data_manager.execute_query(query)
                
                if result is not None and not result.empty:
                    # Cache the result
                    st.session_state.data_cache[cache_key] = result
                    return result
                else:
                    return None
                    
        except Exception as e:
            st.error(f"❌ Failed to load service costs: {str(e)}")
            return None
    
    def render_cost_summary_metrics(self, data: pd.DataFrame, days: int = 30):
        """Render summary cost metrics at the top of the overview."""
        # Split data into current and previous periods
        current_data = data[data['PERIOD'] == 'CURRENT']
        previous_data = data[data['PERIOD'] == 'PREVIOUS']
        
        # Calculate totals for current period
        total_credits = current_data['TOTAL_CREDITS'].sum()
        total_compute_credits = current_data['COMPUTE_CREDITS'].sum()
        total_cloud_services_credits = current_data['CLOUD_SERVICES_CREDITS'].sum()
        active_services = current_data['SERVICE_TYPE'].nunique()
        
        # Calculate period-over-period change
        total_prev_credits = previous_data['TOTAL_CREDITS'].sum()
        
        if total_prev_credits > 0:
            period_change = ((total_credits - total_prev_credits) / total_prev_credits) * 100
        else:
            period_change = 0
        
        # Display metrics in columns
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label=f"Total Credits ({days}d)",
                value=format_credits_with_dollars(total_credits),
                delta=f"{period_change:+.1f}% vs prior {days}d" if period_change != 0 else None
            )
        
        with col2:
            st.metric(
                label="Compute Credits",
                value=format_credits_with_dollars(total_compute_credits),
                delta=None
            )
        
        with col3:
            st.metric(
                label="Cloud Services Credits", 
                value=format_credits_with_dollars(total_cloud_services_credits),
                delta=None
            )
        
        with col4:
            st.metric(
                label="Active Services",
                value=f"{active_services}",
                delta=None
            )
    
    def render_monthly_trends_chart(self, data: pd.DataFrame):
        """Render daily trends chart showing cost evolution over time."""
        
        # Only show current period for the trend chart
        current_data = data[data['PERIOD'] == 'CURRENT'].copy()
        
        # Group data for visualization
        daily_totals = current_data.groupby(['USAGE_DATE', 'SERVICE_TYPE'])['TOTAL_CREDITS'].sum().reset_index()
        
        if daily_totals.empty:
            st.warning("No trend data available")
            return
        
        st.markdown("#### Daily Cost Trends by Service Type")
        
        available_services = sorted(daily_totals['SERVICE_TYPE'].unique().tolist())
        
        with st.expander(f"Filter Service Types ({len(available_services)} available)", expanded=False):
            selected_services = st.multiselect(
                "Select services to include in chart:",
                options=available_services,
                default=available_services,
                key="trend_service_type_filter",
                label_visibility="collapsed"
            )
            st.caption(f"{len(selected_services)} of {len(available_services)} services selected")
        
        # Filter data based on selection
        if selected_services:
            filtered_data = daily_totals[daily_totals['SERVICE_TYPE'].isin(selected_services)]
        else:
            filtered_data = daily_totals
        
        if filtered_data.empty:
            st.warning("No data for selected service types")
            return
        
        # Create fixed color mapping so colors stay consistent when filtering
        color_palette = px.colors.qualitative.Plotly + px.colors.qualitative.Set2 + px.colors.qualitative.Pastel
        color_map = {service: color_palette[i % len(color_palette)] for i, service in enumerate(available_services)}
        
        # Create line chart
        fig = px.line(
            filtered_data,
            x='USAGE_DATE',
            y='TOTAL_CREDITS',
            color='SERVICE_TYPE',
            color_discrete_map=color_map,
            title='Daily Credits Usage by Service Type',
            labels={
                'USAGE_DATE': 'Date',
                'TOTAL_CREDITS': 'Credits Used',
                'SERVICE_TYPE': 'Service Type'
            }
        )
        
        fig.update_layout(
            height=500,
            hovermode='x unified',
            legend=dict(
                orientation="v",
                yanchor="top",
                y=1,
                xanchor="left",
                x=1.02
            )
        )
        
        # Add time range information to chart
        update_chart_with_time_range(
            fig, 
            filtered_data, 
            'USAGE_DATE', 
            'Date', 
            'Daily Credits Usage by Service Type'
        )
        
        fig.update_traces(line=dict(width=3))
        render_plotly_chart(fig)
    
    def render_service_breakdown_charts(self, data: pd.DataFrame):
        """Render service breakdown charts showing current period distribution."""
        st.markdown("#### Current Period Service Breakdown")
        
        # Use current period data only
        current_data = data[data['PERIOD'] == 'CURRENT'].copy()
        
        if current_data.empty:
            st.warning("No current period data available")
            return
        
        # Get date range for the title
        min_date = pd.to_datetime(current_data['USAGE_DATE']).min()
        max_date = pd.to_datetime(current_data['USAGE_DATE']).max()
        date_range_str = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Pie chart for service distribution
            service_totals = current_data.groupby('SERVICE_TYPE')['TOTAL_CREDITS'].sum().reset_index()
            
            fig_pie = px.pie(
                service_totals,
                values='TOTAL_CREDITS',
                names='SERVICE_TYPE',
                title=f'Credits Distribution by Service ({date_range_str})'
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            render_plotly_chart(fig_pie)
        
        with col2:
            # Bar chart for service comparison
            fig_bar = px.bar(
                service_totals.sort_values('TOTAL_CREDITS', ascending=True),
                x='TOTAL_CREDITS',
                y='SERVICE_TYPE',
                orientation='h',
                title=f'Credits by Service ({date_range_str})',
                labels={'TOTAL_CREDITS': 'Credits Used', 'SERVICE_TYPE': 'Service Type'}
            )
            fig_bar.update_layout(height=400)
            render_plotly_chart(fig_bar)
    
    def render_detailed_data_table(self, data: pd.DataFrame):
        """Render detailed data table with sorting and filtering capabilities."""
        st.markdown("#### Detailed Daily Service Costs")
        
        # Add filters
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Service type filter
            available_services = ['All'] + list(data['SERVICE_TYPE'].unique())
            selected_service = st.selectbox(
                "Filter by Service Type:",
                available_services,
                key="service_filter"
            )
        
        with col2:
            # Period filter
            selected_period = st.selectbox(
                "Filter by Period:",
                ['All', 'CURRENT', 'PREVIOUS'],
                key="period_filter"
            )
        
        with col3:
            # Credits threshold filter
            min_credits = st.number_input(
                "Minimum Credits:",
                min_value=0.0,
                value=0.0,
                step=10.0,
                key="credits_filter"
            )
        
        # Apply filters
        filtered_data = data.copy()
        
        if selected_service != 'All':
            filtered_data = filtered_data[filtered_data['SERVICE_TYPE'] == selected_service]
        
        if selected_period != 'All':
            filtered_data = filtered_data[filtered_data['PERIOD'] == selected_period]
        
        filtered_data = filtered_data[filtered_data['TOTAL_CREDITS'] >= min_credits]
        
        if filtered_data.empty:
            st.warning("No data matches the selected filters")
            return
        
        # Format data for display
        display_data = filtered_data.copy()
        display_data['USAGE_DATE'] = pd.to_datetime(display_data['USAGE_DATE']).dt.strftime('%Y-%m-%d')
        display_data['TOTAL_CREDITS'] = display_data['TOTAL_CREDITS'].round(2)
        display_data['COMPUTE_CREDITS'] = display_data['COMPUTE_CREDITS'].round(2)
        display_data['CLOUD_SERVICES_CREDITS'] = display_data['CLOUD_SERVICES_CREDITS'].round(2)
        
        # Display table
        st.dataframe(
            display_data[['USAGE_DATE', 'SERVICE_TYPE', 'TOTAL_CREDITS', 'COMPUTE_CREDITS', 
                         'CLOUD_SERVICES_CREDITS', 'PERIOD']],
            column_config={
                'USAGE_DATE': 'Date',
                'SERVICE_TYPE': 'Service Type',
                'TOTAL_CREDITS': st.column_config.NumberColumn('Total Credits', format="%.2f"),
                'COMPUTE_CREDITS': st.column_config.NumberColumn('Compute Credits', format="%.2f"),
                'CLOUD_SERVICES_CREDITS': st.column_config.NumberColumn('Cloud Services Credits', format="%.2f"),
                'PERIOD': 'Period'
            },
            use_container_width=True,
            hide_index=True
        )
        
        # Export functionality
        if st.button("📥 Export Data as CSV", key="export_monthly_costs"):
            csv = filtered_data.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"snowflake_monthly_costs_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
    
    def render_service_tab_placeholder(self, service_name: str):
        """Render placeholder content for service-specific tabs."""
        st.markdown(f"""
        ### {service_name} Analysis
        
        This section will provide detailed {service_name.lower()} usage analysis including:
        """)
        
        # Service-specific features
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("**View Options**")
            st.markdown("- Warehouse View")
            st.markdown("- User View") 
            st.markdown("- Client Connection View")
        
        with col2:
            st.markdown("**Analytics**")
            st.markdown("- Monthly Consumption Trends")
            st.markdown("- Cost Optimization Opportunities")
            st.markdown("- Usage Pattern Analysis")
        
        with col3:
            st.markdown("**Features**")
            st.markdown("- Interactive Charts")
            st.markdown("- Data Export Capabilities")
            st.markdown("- Drill-down Analysis")
        
        # Implementation status
        st.info(f"🚧 **{service_name} analyzer implementation coming in next development phase**")
        
        # Preview toggle controls that will be implemented
        st.markdown("**Preview of Toggle Controls:**")
        view_type = st.radio(
            f"Select {service_name} view:",
            ["Warehouse", "User", "Client Connection"],
            key=f"{service_name.lower().replace(' ', '_')}_view_toggle",
            horizontal=True,
            disabled=True
        )
        st.caption("Toggle controls will be functional in the next implementation phase.")
    
    def render_storage_tab(self):
        """Render the Storage analysis tab using StorageAnalyzer."""
        storage_analyzer = StorageAnalyzer(self.data_manager)
        storage_analyzer.render_analysis()
    
    def render_consumption_tab(self):
        """Render the Consumption analysis tab using ConsumptionAnalyzer."""
        consumption_analyzer = ConsumptionAnalyzer(self.data_manager)
        consumption_analyzer.render_analysis()
    
    def render_cloud_services_tab(self):
        """Render the Cloud Services analysis tab."""
        if not hasattr(self, '_cloud_services_analyzer'):
            self._cloud_services_analyzer = CloudServicesAnalyzer(self.data_manager)
        
        self._cloud_services_analyzer.render_analysis()
    
    def render_replication_tab(self):
        """Render the Replication analysis tab."""
        if not hasattr(self, '_replication_analyzer'):
            self._replication_analyzer = ReplicationAnalyzer(self.data_manager)
        
        self._replication_analyzer.render_analysis()
    
    def render_clustering_tab(self):
        """Render the Clustering analysis tab."""
        if not hasattr(self, '_clustering_analyzer'):
            self._clustering_analyzer = ClusteringAnalyzer(self.data_manager)
        
        self._clustering_analyzer.render_analysis()
    
    def render_serverless_tab(self):
        """Render the Serverless analysis tab."""
        if not hasattr(self, '_serverless_analyzer'):
            self._serverless_analyzer = ServerlessAnalyzer(self.data_manager)
        
        self._serverless_analyzer.render_analysis()
    
    def render_ai_services_tab(self):
        """Render the AI Services analysis tab with simplified, accurate cost tracking."""
        if not hasattr(self, '_ai_services_analyzer'):
            self._ai_services_analyzer = AIServicesAnalyzer(self.data_manager)
        
        self._ai_services_analyzer.render_analysis()
    
    def render_client_consumption_tab(self):
        """Render the Client Consumption analysis tab."""
        if not hasattr(self, '_client_consumption_analyzer'):
            self._client_consumption_analyzer = ClientConsumptionAnalyzer(self.data_manager)
        
        self._client_consumption_analyzer.render_analysis()
    
    def render_footer(self):
        """Render application footer."""
        st.markdown("---")
        st.markdown("""
        <div style='text-align: center; color: gray; font-size: 0.8em;'>
            Snowflake Cost Monitoring Dashboard | Built with Streamlit | 
            Data Source: ACCOUNT_USAGE Schema<br>
            <em>Disclaimer: Cost estimates are approximate and not official Snowflake billing calculations.</em>
        </div>
        """, unsafe_allow_html=True)
    
    def handle_navigation(self):
        """Handle navigation state and maintain application performance."""
        # Navigation is handled through session state in render_sidebar()
        # This method can be extended for additional navigation logic
        pass
    
    def run(self):
        """Main application entry point."""
        # Handle navigation state
        self.handle_navigation()
        
        # Render sidebar navigation
        self.render_sidebar()
        
        # Render main content based on selected tab
        self.render_main_content()
        
        # Render footer
        self.render_footer()


def main():
    """Application entry point."""
    # Configure Streamlit page
    st.set_page_config(
        page_title="Snowflake Cost Dashboard",
        page_icon="",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Initialize and run the dashboard
    dashboard = SnowflakeUsageDashboard()
    dashboard.run()


if __name__ == "__main__":
    main()
