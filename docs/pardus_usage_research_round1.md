# Pardus 纯使用调研（Round 1）

更新时间：2026-02-08

## 调研边界

- 本文只记录“怎么用、用了什么、产出了什么、边界在哪”。
- 不做产品设计方案、不做后端接口复刻建议。

## 一、来自 @lidangzzz 与 @EinNewton 的可执行线索

### 1) @lidangzzz（用户侧）

高价值线索（优先级高）：

1. 明确提到用真实数据源做测试：
   - **Indian Liver Disease Dataset**
   - **血液检查报告（blood test report）**
   - **NYC Real Estate Sales（Kaggle）**
   - **Global Box Office Dataset（Kaggle）**
   - **乳腺癌数据（breast cancer）**
2. 明确提到“上传即分析、低提示词依赖”的使用方式。
3. 明确提到把结果作为“可视化 + 结论摘要”直接消费。

来源：
- https://twstalker.com/lidangzzz
- https://twstalker.com/lidangzzz/status/2012327582738378977
- https://twstalker.com/lidangzzz/status/2012347657579206934
- https://twstalker.com/lidangzzz/status/2013205707500398668

### 2) @EinNewton（作者侧）

高价值线索（优先级高）：

1. 持续发布功能迭代，而不是一次性演示：
   - web search
   - public project / share
   - v1.3
   - agent v2.0
2. 公开案例入口大量指向 `/view/{project_id}`。
3. 公开项目覆盖多行业和多任务（预测、分类、相关性、地理分布、报告总结）。

来源：
- https://x.com/EinNewton
- https://twstalker.com/EinNewton

## 二、可直接复用的数据源清单（按 lidang 使用方式）

1. Kaggle（优先）：NYC Real Estate、Global Box Office、Titanic、YouTube、AI Jobs
2. UCI Repository：Indian Liver Disease、Breast Cancer Wisconsin
3. 公开统计站点：World Bank / UN / NOAA / IEA（宏观时序）
4. 本地业务 CSV：销售、库存、履约、客服、投放（最贴近真实复刻场景）

## 三、已完成的产品使用记录（公开案例浏览，非上传闭环）

说明：
- 本轮全部基于 Pardus 公开项目页面 `/view/{id}` 的真实浏览使用。
- 每次至少检查：主题、分析结构、输出形态（图/结论/建议）。
- 这不等于“我上传数据并完成一次完整分析流程”。

### Batch A（1-10）

1. `4132c2f7f6e14e7ca2f0b9c997bc7b5f` Weekly sales forecast and inventory
   - 观察到：周级销售预测、库存建议、运营动作建议。
2. `4d922f2f578f44d0998808eb1ac6e8a7` Regulatory changes & LinkedIn sentiment
   - 观察到：文本/舆情 + 监管主题综合分析。
3. `676ab13f402f4a009f94546c2ea1fd31` Climate analysis (6 years)
   - 观察到：时序趋势 + 变化分段叙述。
4. `6b8f1546ad8e4225acc5d1d4e7f7b84a` Town/community analysis report
   - 观察到：区域主题报告风格。
5. `20f9cb72820a4fb7b8ad4f7e5deb4f96` Social intelligence report
   - 观察到：相关性（Pearson）+ 分布 + 实操建议。
6. `b74e8f589f6f42a0a2b6ec097acff4f3` Steam game distribution & recommendation
   - 观察到：分布图、评分结构、推荐逻辑。
7. `9584c04533914d23bdbb7f4d81280fc8` Titanic survival analysis
   - 观察到：生存分类、分组特征解释、关键变量洞察。
8. `4d31e7abc3a24883ace0e17068953c2c` Blood test dataset analysis
   - 观察到：医学指标相关性、风险提示、解释文本。
9. `9d4ca1326a42466183531d4867701ce7` AI jobs & salary trend
   - 观察到：岗位、薪资、技能需求趋势。
10. `b3361f87cf2f4768a245f3f2f21084c7` Top 50 movies by revenue
   - 观察到：票房结构、类别对比、可视化报告。

### Batch B（11-20）

11. `27c466adb53e4b6fabec84f0008a1f80` Retail sales performance
    - 观察到：经营指标拆解、时序对比。
12. `5f5dc5cc8cd74a928f9072243507bbd6` Web browser usage trends
    - 观察到：份额变化、平台迁移趋势。
13. `497517bb80de44ffae5a6de055208c2d` Top 500 YouTubers
    - 观察到：频道层级、订阅/观看关系。
14. `3e357413a7034dcab53c29d3daca7ed8` Top 100 YouTubers metrics
    - 观察到：相关性矩阵、分布、分段解释。
15. `9f72b5e5801543739f5ff2727e90395c` Weather data report
    - 观察到：气候波动、年份间比较。
16. `c0848d21caad43d996268f6be8e00e28` Electricity production/consumption
    - 观察到：供需关系、能源结构对比。
17. `9f4ee307778a4f3f81f91f72395f3e8d` India crop production trends
    - 观察到：时空（区域+时间）分析、相关关系。
18. `cec5f6f9f5a4412abef8ceeb0f2ca59c` Project analysis report
    - 观察到：标准化报告模板（概览→图表→结论）。
19. `14634dc0b68f4e849f44d54fbe6aaed2` NYC Airbnb analysis
    - 观察到：地理分布、价格与区域特征、建议输出。
20. `e30af462a0e84e43ace1f94f7f673de5` Death in custody study
    - 观察到：统计概览、异常组识别、政策建议。

### Batch C（21-50，快速通读）

说明：
- 基于 `https://pardusai.org/v1/api/public/projects/recent?limit=50` 的最近 50 个公开项目，继续打开剩余 30 个 `/view/{id}` 页面通读。
- 每个页面至少检查：是否为“完整报告页”、是否包含可读分析段落、是否含图表/统计结论/建议段。

21-50 的主题覆盖（按内容聚类）：

1. 金融与预测：Stock Price、House Price、市场价格洞察、能源消费趋势
2. 医疗健康：免疫不良事件、医院入院数据、心理健康、血检类延伸主题
3. 社会与公共政策：选民投票行为、乌克兰冲突伤亡、人口与社会经济趋势
4. 商业运营：电信流失（Churn）、营销活动效果、区域销售利润、零售篮子分析
5. 教育与个体行为：学生成绩、情绪与人格数据分析
6. 环境与生态：鸟类迁徙/观测、可持续发展与经济增长
7. 安全与技术：网络安全威胁检测与响应时延分析

快速通读共性记录：

1. 页面均是“报告化表达”而非仅图表画布。
2. 结构基本稳定：概览 -> 数据特征 -> 图表解释 -> 结论/建议。
3. 统计术语出现频率高（相关性、趋势、分布、异常、预测）。
4. 业务建议文本在多数页面中存在，且通常放在结尾段。

## 四、本轮只记录“使用事实”的共性（非设计建议）

1. 输出结构高度稳定：
   - 背景/目标
   - 数据概览
   - 图表分析
   - 关键结论
   - 建议/行动项
2. 公开项目普遍具备“可直接阅读的报告文本 + 图表化证据”。
3. 任务覆盖面广：
   - 预测类
   - 相关性类
   - 分组对比类
   - 地理/时序类
4. 医疗、社会、商业、能源等主题都能产出可读报告（说明其输入并不局限于单一行业）。

## 五、下一轮执行（继续只做使用）

1. 公开项目浏览已完成 **50 次**（仅浏览与结构观察）；下一轮重点转到“自带数据上传”的真实闭环体验。
2. 用 lidang 提到的 5 类数据源做“同题复现”体验（上传路径）。
3. 对每次使用固定记录：
   - 输入源
   - 用时
   - 输出类型
   - 失败/降级表现
   - 结果可分享性
