# 🎬 TMDB Movies Dataset - Starter Notebook

A comprehensive Jupyter notebook providing a quick start guide for analyzing the TMDB Movies Dataset (21,080+ movies).

---

## 📋 Overview

This starter notebook demonstrates basic data exploration, cleaning, and visualization techniques for the TMDB Movies Dataset. It's designed to help you quickly understand the dataset structure and start your own analysis.

---

## 🗂️ Notebook Structure

### 1. **Import Libraries**
```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
```

Essential libraries for data manipulation and visualization.

---

### 2. **Load Data and Information**

**Load the dataset:**
```python
df = pd.read_csv("tmdb_movies_cleaned.csv")
```

**Basic exploration commands:**
- `df.head()` - View first few rows
- `df.shape` - Get dataset dimensions (21,080 rows × 9 columns)
- `df.info()` - Check data types and non-null counts
- `df.isnull().sum()` - Identify missing values
- `df.dtypes` - View column data types

**Key Findings:**
- Total movies: 21,080
- Missing values: 46 in `release_date` column
- Data types: Mix of boolean, float, int, and object types

---

### 3. **Data Cleaning**

**Parse Genre IDs:**
```python
import ast
df['genre_ids'] = df['genre_ids'].apply(
    lambda x: ast.literal_eval(x) if pd.notna(x) else []
)
```
Converts string representation of genre IDs to actual Python lists.

**Convert Release Dates:**
```python
df['release_date'] = pd.to_datetime(df['release_date'], errors='coerce')
df['release_year'] = df['release_date'].dt.year
```
Creates a datetime column and extracts the year for easier analysis.

---

### 4. **Data Visualization**

#### **Top Rated Movies**
```python
top_movies = df[df['vote_average'] >= 10].head(10)
```
Displays movies with perfect 10.0 ratings.

**Bar Chart:**
Horizontal bar chart showing top-rated movies and their average ratings.

---

#### **Rating Distribution**
```python
plt.hist(df['vote_average'], bins=20, density=True, edgecolor='black')
sns.kdeplot(df['vote_average'], fill=True)
```

**What it shows:**
- Histogram of rating frequencies
- KDE (Kernel Density Estimate) overlay for smooth distribution
- Most movies cluster around 6-7 rating range

---

#### **Vote Count vs Rating Scatter Plot**
```python
sns.scatterplot(x=df['vote_count'], y=df['vote_average'])
```

**Insights:**
- Relationship between number of votes and average rating
- Movies with more votes tend to have more reliable ratings
- Identifies potential outliers and patterns

---

## 🎯 Key Takeaways from the Notebook

### ✅ What's Included:
1. **Data loading** and basic exploration
2. **Data cleaning** (parsing genres, converting dates)
3. **Basic visualizations** (bar charts, histograms, scatter plots)
4. **Top movies** identification

### 💡 What to Explore Next:
- Genre analysis and distribution
- Language-wise movie statistics
- Popularity trends over time
- Correlation analysis between features
- Time series analysis of releases

---

## 🚀 Getting Started

### Prerequisites
```python
pip install pandas numpy matplotlib seaborn
```

### Running the Notebook
1. Ensure you have the dataset file: `tmdb_movies_cleaned.csv`
2. Open Jupyter Notebook or JupyterLab
3. Run cells sequentially from top to bottom

---

## 📊 Sample Outputs

### Data Structure
```
<class 'pandas.core.frame.DataFrame'>
RangeIndex: 21080 entries, 0 to 21079
Data columns (total 9 columns):
 #   Column             Non-Null Count  Dtype  
---  ------             --------------  -----  
 0   adult              21080 non-null  bool   
 1   original_language  21080 non-null  object 
 2   original_title     21080 non-null  object 
 3   popularity         21080 non-null  float64
 4   release_date       21034 non-null  object 
 5   video              21080 non-null  bool   
 6   vote_average       21080 non-null  float64
 7   vote_count         21080 non-null  int64  
 8   genre_ids          21080 non-null  object 
```

### Missing Values
- `release_date`: 46 missing entries
- All other columns: Complete

---

## 🎓 Learning Objectives

By working through this notebook, you will learn:
- How to load and inspect movie datasets
- Basic data cleaning techniques (parsing JSON-like strings, date conversion)
- Creating informative visualizations
- Identifying top-performing movies
- Understanding rating distributions

---

## 🔧 Customization Tips

1. **Change the rating threshold:**
   ```python
   top_movies = df[df['vote_average'] >= 8.5].head(20)
   ```

2. **Filter by specific year:**
   ```python
   movies_2020 = df[df['release_year'] == 2020]
   ```

3. **Analyze specific genres:**
   ```python
   action_movies = df[df['genre_ids'].apply(lambda x: 28 in x)]
   ```

---

## 📝 Notes

- **Date Parsing Issue:** The notebook shows dates being converted to 1970-01-01, which suggests a data quality issue that needs investigation
- **Genre IDs:** Genre IDs are stored as lists. Use the genre mapping provided in the main README for interpretation
- **Perfect Ratings:** Movies with 10.0 ratings often have very few votes (vote_count = 1-3), indicating potential bias

---

## 🤝 Contributing

This is a starter notebook! Feel free to:
- Add more visualizations
- Explore different aspects of the data
- Create predictive models
- Share your findings in the comments

---

## 📚 Next Steps

After mastering this notebook, try:
1. Building a movie recommendation system
2. Creating a rating prediction model
3. Analyzing genre trends over decades
4. Comparing languages and regional patterns

---

**Happy Analyzing! 🎬📊**

For the complete dataset and documentation, visit the main Kaggle page.

---

## 🐛 Known Issues

- Date conversion issue needs investigation (all dates converting to 1970)
- Some movies have 0 votes and 0 ratings (unreleased movies)
- Genre mapping requires external reference

---

*This notebook is part of the TMDB Movies Dataset project. For questions or suggestions, please comment on the Kaggle page.*