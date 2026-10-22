# zhuyin-fix：注音亂碼修正引擎（原型）

把「忘記切換注音輸入法」打出來的英數亂碼（大千配置）還原成中文。
純 Python 標準函式庫，不需安裝任何套件。

```
$ python zhuyin_fix.py "eji ru8 u/3"
音節： ㄍㄨㄛ ㄐㄧㄚ ㄧㄥˇ
信心： 1.0
  1. 郭佳穎   (郭 | 佳穎)
  2. 鍋佳穎   (鍋 | 佳穎)
  3. 郭家影   (郭 | 家 | 影)
  ...

$ python zhuyin_fix.py --text "我朋友叫eji ru8 u/3, she is nice"
我朋友叫郭佳穎, she is nice
```

## 檔案

| 檔案 | 用途 |
|---|---|
| `zhuyin_fix.py` | 引擎本體與命令列工具 |
| `zhuyin_dict.json` | 詞庫（由 `build_dict.py` 產生，約 17 萬筆） |
| `build_dict.py` | 從 McBopomofo 原始資料重建詞庫 |
| `eval.py` | 測試集與命中率評估 |

## 在程式中使用

```python
from zhuyin_fix import ZhuyinFixer

engine = ZhuyinFixer(user_dict_path="user_dict.json")

engine.decode("su3cl3")                 # 整串都是亂碼（手動選取模式）
# -> [Candidate(text='你好', ...), Candidate(text='妳好', ...), ...]

engine.fix_text("hi su3cl3 ok")         # 自動找出句子中的亂碼片段
# -> ('hi 你好 ok', [{'raw': 'su3cl3', 'confidence': 1.0, 'candidates': [...]}])

cands = engine.decode("eji ru8 u/3")
engine.select("eji ru8 u/3", cands[2])  # 使用者選了第三個候選
engine.select("eji ru8 u/3", "郭家穎")  # 或直接輸入正確文字（不在候選中也可以）
engine.forget("郭家穎")                 # 刪除學錯的詞
```

互動模式可以直接體驗學習效果：

```
$ python zhuyin_fix.py --interactive
```

## 依使用習慣調整候選（像一般注音輸入法）

每次使用者確認答案，都會記錄到 `user_dict.json`，下次排序時加分：

| 學習內容 | 效果 | 例子 |
|---|---|---|
| 選字次數 | 選越多次分數越高，但會遞減，不會一次就鎖死 | 常打「妳好」的人，`su3cl3` 第一名變成「妳好」 |
| 最近使用 | 最近用過的再加分，約一個月後衰減 | 習慣改變時，新的選擇會逐漸取代舊的 |
| 自動學詞 | 選了第一名以外的候選，整串會學成一個新詞 | 選過「郭家穎」後，夾在句子中也會正確轉換 |
| 前後文 | 記住常一起出現的詞（bigram） | 學過「記得帶傘」後，「誰帶傘」也不會再變成「誰代傘」 |

學習只影響選過的詞：學了「妳好」不會讓「你們」變成「妳們」。
`python eval.py` 的最後一段示範了這些情境。

## 運作流程

1. **鍵位對應**：按鍵轉成注音符號（`e` → ㄍ）。
2. **音節切分**：以聲調鍵（空白、6、3、4、7）切開，比對 1,394 個合法音節；
   順序打錯（聲母、介音、韻母顛倒）會自動重排，但扣分。
3. **亂碼偵測**：合法音節比例、是否為常見英文字、長度。單一音節（例如 `u3`）
   在自動模式不處理，避免誤判。
4. **選字**：k-best Viterbi，以詞頻（unigram）計分，每多切一個詞扣分以偏好長詞；
   多音字依 McBopomofo 的讀音優先順序調整字頻。

## 目前成績（`python eval.py`）

| 測試 | 第一候選 | 前五候選 |
|---|---|---|
| 日常句子（47 句） | 94% | 98% |
| 人名（7 個） | 57% | 57% |
| 英文誤判（12 句） | 0 句被改掉 | |

## 已知限制與下一步

- **人名**：同音名字（郭家穎 / 郭佳穎）本質上無法判斷，靠 `select()` 記住使用者的選擇，
  之後可加入匯入聯絡人。
- **系統詞庫只看單詞頻率**：沒學過的情況下，「記得帶傘」會選成「記得代傘」。
  使用者的前後文會被學習，但系統本身還沒有前後文模型；下一步可加入通用的 bigram 語言模型，
  或用 LLM 重新排序前五名。
- **習慣檔不分程式**：之後可以依程式分開（例如在工作用的 Teams 和私人的 LINE 習慣不同）。
- **跨聲調的順序錯誤**：`s3u`（聲調先打）目前無法還原，只能修正音節內的順序。
- **僅支援大千配置**：倚天、許氏需要另外的鍵位表。

## 授權

程式碼採 MIT License，見 `LICENSE`。

詞庫資料（`zhuyin_dict.json`）來自 [McBopomofo 小麥注音輸入法](https://github.com/openvanilla/McBopomofo)
（MIT License, Copyright (c) 2011-2026 Mengjuei Hsieh et al.），
完整授權條款見 `LICENSE-McBopomofo.txt`。
