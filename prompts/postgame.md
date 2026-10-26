根据下面的数据写一份「赛后报告」，发到 fantasy 群里。只能用数据里的信息，数字照抄。

结构（用 Markdown，标题简短）：
1. **标题**：一句带梗的标题，含日期。
2. **比赛结果速览**：每场一行（客队 比分 @ 主队 比分，用中文队名），加时注明。
3. **焦点比赛点评**：focus_games 的 2~3 场，每场两三句，结合 reasons 和该场 top_players。
4. **数据高光**：top_performances 里的 3~5 人，每人一行数据 + 一句点评；如果 fantasy_manager 不为空，直接点名恭喜（或调侃）这位经理。
5. **今日伤病**：todays_injuries 里的情况，每人一行（中文写伤病部位和状态，一句话）。
   - in_game 为 true 的是比赛中受伤 / 提前退场，放最前面重点写；其余是今天公布的伤停。
   - fantasy_manager 不为空时点名提醒这位经理（比如"James 注意了"）。
   - 列表为空就写一句"今天没有新增伤病"。
6. **Fantasy 战况**：
   - 当前排名前几和垫底的队，领先多少（standings）。排名变化只能根据 prev_rank（上次排名）和 rank 对比来说，比如"从第 3 升到第 1"；prev_rank 和 rank 相同就不要说"易主""反超"。
   - 涨分最多 / 掉分最多的队（movers；change 的含义见 standings_change_basis，照它的口径说，比如"今天"还是"上个比赛日"）。
   - fantasy_duds 里拖后腿的球员，点名调侃一下对应的经理（fantasy_manager）。
   - close_battles：分项上咬得最紧的争夺，点名提醒相关经理（leader_manager / chaser_manager）。

要求：全文控制在 1000 字以内。只写数据里有的信息，标题也不能有数据之外的说法；某部分数据缺失或报错，直接说明，别编。

数据（JSON）：
```json
{data}
```
