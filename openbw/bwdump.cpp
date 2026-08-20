/* OpenBW 헤드리스 리플레이 덤퍼 — 프레임마다 유닛 상태를 내보낸다.
 *   bwdump <자료폴더> <리플레이.rep> [프레임간격]
 * 자료폴더에는 MPQ가 아니라 **풀어 놓은 파일 열 개**만 있으면 된다:
 *   arr/{units,weapons,upgrades,techdata,flingy,sprites,images,orders}.dat
 *   arr/images.tbl · scripts/iscript.bin
 * 그림·소리는 한 장도 안 쓴다(시뮬레이션만 한다). */
#include "replay.h"
#include "modern_replay.h"
#include <cstdio>
#include <string>
#include <cstring>
#include <zlib.h>
#include <map>
#include <vector>
#include <algorithm>
#include <set>
#include <cmath>

static int g_ok[64] = {0}, g_slot[64] = {0}, g_tot[64] = {0};
/** 고르기가 유닛을 찾았나 — 1분 칸으로 모은다(23.81프레임 = 1초). */
static int g_first[256], g_cnt[256];
/** 명령 갈래마다 **처음 나온 프레임**을 적어 둔다 — 어긋남이 시작되는 시각과 견주면
 *  "그 무렵 처음 쓰인 명령"이 범인 후보로 좁혀진다. */
void bwdump_action(int id, int frame) {
  id &= 0xff;
  if (!g_cnt[id]) g_first[id] = frame;
  g_cnt[id] += 1;
}
/* 세대(generation) 어긋남 재기 — 리플레이가 적어 온 세대와 우리가 센 세대의 **차이**를
   분 단위로 모은다. 차이가 늘 같은 수면 세는 시점만 다른 것이고(고치면 끝), 시각이
   갈수록 흩어지면 유닛이 나고 죽는 차례가 진짜로 갈라진 것이다. */
static std::map<int,int> g_gen[64];
static int g_gen_tot[64] = {0};
static std::map<int,int> g_pair;
void bwdump_gen(int frame, unsigned want, unsigned got, unsigned kind) {
  (void)kind;
  int b = frame / 1429; if (b > 63) b = 63;
  g_gen[b][(int)want - (int)got] += 1;
  /* 짝 그대로도 센다 — "우리는 한 번도 안 쓴 자리(0)인데 리플레이는 2라고 한다"면
     실제 게임이 그 자리를 두 번 더 돌려 쓴 것이고, 그건 우리가 안 죽인 유닛이 있다는 뜻이다. */
  g_pair[((int)got << 8) | (int)(want & 0xff)] += 1;
  g_gen_tot[b] += 1;
}
static void bwdump_gen_report() {
  if (!getenv("BWDUMP_GEN")) return;
  {
    fprintf(stderr, "\n(우리 세대 → 리플레이 세대) 짝, 많은 차례로\n");
    std::vector<std::pair<int,int>> v(g_pair.begin(), g_pair.end());
    std::sort(v.begin(), v.end(), [](const std::pair<int,int>& a, const std::pair<int,int>& c){ return a.second > c.second; });
    int tot = 0; for (auto& e : v) tot += e.second;
    for (size_t i = 0; i < v.size() && i < 14; i += 1)
      fprintf(stderr, "  %2d → %-3d  %5d회 (%.1f%%)%s\n", v[i].first >> 8, v[i].first & 0xff,
        v[i].second, v[i].second * 100.0 / tot, (v[i].first >> 8) == (v[i].first & 0xff) ? "  맞음" : "");
  }
  fprintf(stderr, "\n세대 차이(리플레이가 적은 값 − 우리가 센 값), 분마다 앞 4가지\n");
  for (int b = 0; b < 64; b += 1) {
    if (!g_gen_tot[b]) continue;
    std::map<int,int>& m = g_gen[b];
    std::vector<std::pair<int,int>> v(m.begin(), m.end());
    std::sort(v.begin(), v.end(), [](const std::pair<int,int>& a, const std::pair<int,int>& c){ return a.second > c.second; });
    fprintf(stderr, "  %2d분  n=%-5d ", b, g_gen_tot[b]);
    for (size_t i = 0; i < v.size() && i < 4; i += 1)
      fprintf(stderr, "  %+d:%.0f%%", v[i].first, v[i].second * 100.0 / g_gen_tot[b]);
    fprintf(stderr, "   (갈래 %d)\n", (int)v.size());
  }
}

static std::map<std::string,int> g_trigmiss;
void bwdump_trigmiss(int kind, int id) {
  char k[48]; snprintf(k, sizeof k, "%s %d", kind == 1 ? "조건" : "동작", id);
  g_trigmiss[k] += 1;
}
static std::map<std::string,int> g_ai;
void bwdump_aiorder(const char* name) { g_ai[name] += 1; }
static void bwdump_trigmiss_report() {
  if (g_trigmiss.empty()) return;
  fprintf(stderr, "\n⚠ 안 만든 트리거를 만났다(OpenBW 미구현 — 조건은 '아니다', 동작은 건너뜀)\n");
  for (const auto& kv : g_trigmiss) fprintf(stderr, "   %-10s %d회\n", kv.first.c_str(), kv.second);
}
static void bwdump_ai_report() {
  if (g_ai.empty()) return;
  fprintf(stderr, "\n⚠ 컴퓨터 AI 명령이 나왔다(OpenBW에 구현 없음 — '가만히'로 넘겼다)\n");
  for (const auto& kv : g_ai) fprintf(stderr, "   %-16s %d회\n", kv.first.c_str(), kv.second);
  fprintf(stderr, "   → 이 판의 참값은 그만큼 못 믿는다\n");
}
static std::map<std::string,int> g_why;
void bwdump_why(const char* what, int step) {
  if (!getenv("BWDUMP_WHY")) return;
  char k[64]; snprintf(k, sizeof k, "%s#%d", what, step);
  g_why[k] += 1;
}
static int g_when = 0;
void bwdump_when(const char* what, int frame, int owner, int extra) {
  if (!getenv("BWDUMP_WHEN")) return;
  if (strstr(what, ":") == nullptr) return;      /* 실패만 */
  if (g_when++ >= 24) return;
  fprintf(stderr, "  %7.1f초  임자%d  %s (곁수 %d)\n", frame / 23.81, owner, what, extra);
}
static std::map<std::string,int> g_own;
void bwdump_owner(const char* what, int co, int uo) {
  if (!getenv("BWDUMP_WHY")) return;
  char k[80]; snprintf(k, sizeof k, "%-10s 명령임자%d → 유닛임자%d", what, co, uo);
  g_own[k] += 1;
}
static void bwdump_why_report() {
  if (!getenv("BWDUMP_WHY")) return;
  fprintf(stderr, "\n생산 명령이 어디서 막히나 (#0=들어온 횟수, 나머지=막힌 관문)\n");
  for (const auto& kv : g_why) fprintf(stderr, "  %-12s %6d\n", kv.first.c_str(), kv.second);
  fprintf(stderr, "\n임자별로\n");
  for (const auto& kv : g_own) fprintf(stderr, "  %-40s %6d\n", kv.first.c_str(), kv.second);
}
static std::map<int,int> g_trig;
void bwdump_trig(int type) { g_trig[type] += 1; }
static void bwdump_trig_report() {
  if (!getenv("BWDUMP_TRIG")) return;
  fprintf(stderr, "\n트리거 (−1 = 트리거 도는 횟수)\n");
  for (const auto& kv : g_trig) fprintf(stderr, "  동작 %3d  %7d회\n", kv.first, kv.second);
  if (g_trig.empty()) fprintf(stderr, "  한 번도 안 돔\n");
}
int bwdump_cur_owner = -1;
static int g_po[12][64] = {{0}}, g_pt[12][64] = {{0}};
static void bwdump_owner_time_report() {
  if (!getenv("BWDUMP_PT")) return;
  fprintf(stderr, "\n임자별 · 분별 고르기 적중률\n     ");
  for (int b = 0; b < 16; ++b) fprintf(stderr, "%6d분", b);
  fprintf(stderr, "\n");
  for (int p = 0; p < 12; ++p) {
    int any = 0; for (int b = 0; b < 64; ++b) any += g_pt[p][b];
    if (!any) continue;
    fprintf(stderr, "  임자%d", p);
    for (int b = 0; b < 16; ++b)
      if (g_pt[p][b]) fprintf(stderr, "%5.0f%%%c", g_po[p][b] * 100.0 / g_pt[p][b], ' ');
      else fprintf(stderr, "      -");
    fprintf(stderr, "\n");
  }
}
static std::map<std::string,int> g_fail;
static std::map<std::string,int> g_fail_first;
void bwdump_fail(const char* why, int frame, int id) {
  g_fail[why] += 1;
  if (frame >= 0 && !g_fail_first.count(why)) g_fail_first[why] = frame;
}
static void bwdump_fail_report() {
  if (g_fail.empty()) { fprintf(stderr, "\n유닛 만들기가 막힌 적 없음\n"); return; }
  fprintf(stderr, "\n유닛·그릇 만들기 실패\n");
  for (const auto& kv : g_fail)
    fprintf(stderr, "  %-32s %6d회  처음 %.1f초\n", kv.first.c_str(), kv.second,
      g_fail_first.count(kv.first) ? g_fail_first[kv.first] / 23.81 : -1.0);
}
/* 겨냥 어긋남 — 리플레이의 우클릭·표적명령에는 표적 태그와 **그때의 좌표**가 함께 실린다.
   사람은 유닛 그림 위를 찍으므로 그 좌표는 실제 게임에서 그 유닛이 있던 자리다. 우리 시뮬이
   아는 자리와 얼마나 벌어지나를 재면, 고르기 적중률이 100%인 구간에서도 미세한 밀림이 보인다.
   이것이 갈리기 훨씬 전부터 커지고 있으면 '서서히 쌓인 것'이고, 갈리는 순간까지 붙어 있으면
   '한 번의 사건'이다. */
static std::vector<int> g_aim[64];
void bwdump_aim(int frame, int dx, int dy) {
  int b = frame / 1429; if (b > 63) b = 63;
  g_aim[b].push_back((int)(std::sqrt((double)dx * dx + (double)dy * dy) + 0.5));
}

/* 참값을 어디까지 믿어도 되나 ─────────────────────────────────────────────────
   두 가지 신호를 함께 본다.
    ① 고르기 적중률 — 리플레이 명령이 가리킨 유닛을 시뮬이 찾아내는 비율
    ② 겨냥 어긋남 — 리플레이가 적어 둔 표적 좌표와 우리 시뮬의 그 유닛 자리 차이
   둘 다 **두 분 잇달아** 나빠야 갈린 것으로 본다. 한 분만 나쁜 것은 큰 전투다 — 방금 죽은
   유닛을 가리키는 명령이 몰리면 적중률이 잠깐 떨어졌다가 돌아온다. 1.16 판 20분이 그랬는데,
   한 분만 보는 문턱은 23분 내내 멀쩡한 판을 갈렸다고 잘못 읽었다(못 찾은 26건이 전부
   6~13프레임 전에 죽은 유닛이었다). 진짜 갈림은 안 돌아온다. */
static double bwdump_aim_p90(int b) {
  if (g_aim[b].size() < 8) return -1;
  std::vector<int> v = g_aim[b];
  std::sort(v.begin(), v.end());
  return v[(size_t)(v.size() * 0.9)];
}
static bool g_no_ai = false;
static int bwdump_trust_frame() {
  if (g_no_ai) return 0;      /* 컴퓨터가 낀 판은 처음부터 못 믿는다 */
  for (int b = 0; b + 1 < 64; ++b) {
    const bool res_bad = g_tot[b] >= 20 && g_tot[b + 1] >= 20
      && g_ok[b] * 100 < g_tot[b] * 97 && g_ok[b + 1] * 100 < g_tot[b + 1] * 97;
    const double a0 = bwdump_aim_p90(b), a1 = bwdump_aim_p90(b + 1);
    const bool aim_bad = a0 > 120 && a1 > 120;
    if (res_bad || aim_bad) return b * 1429;
  }
  return -1;
}
void bwdump_aim_big(int frame, unsigned tag, int kind, int owner, int ox, int oy, int rx, int ry) {
  if (!getenv("BWDUMP_AIMBIG")) return;
  const int lim = atoi(getenv("BWDUMP_AIMBIG"));
  const double d = std::sqrt((double)(rx - ox) * (rx - ox) + (double)(ry - oy) * (ry - oy));
  if (d < lim) return;
  /* 창(window)을 주면 그 구간만 본다 — BWDUMP_AIMFROM/AIMTO(초) */
  const double sec = frame / 23.81;
  if (getenv("BWDUMP_AIMFROM") && sec < atof(getenv("BWDUMP_AIMFROM"))) return;
  if (getenv("BWDUMP_AIMTO") && sec > atof(getenv("BWDUMP_AIMTO"))) return;
  static int n = 0;
  if (n++ >= 60) return;
  fprintf(stderr, "  %7.1f초 · 태그 %u(자리 %u 세대 %u) 종류 %d 임자 %d · 우리 (%d,%d) vs 리플레이 (%d,%d) · %.0f픽셀\n",
    frame / 23.81, tag, tag & 0x1fff, tag >> 13, kind, owner, ox, oy, rx, ry, d);
}
static int g_so_bad[64], g_so_tot[64];
static int g_so_shown = 0;
void bwdump_selown(int frame, int co, int uo, int kind, unsigned tag) {
  int b = frame / 1429; if (b > 63) b = 63;
  g_so_tot[b] += 1;
  if (co == uo) return;
  g_so_bad[b] += 1;
  if (getenv("BWDUMP_SELOWN") && g_so_shown++ < 16)
    fprintf(stderr, "  %7.1f초 · 임자%d가 고른 유닛이 우리 시뮬에선 임자%d의 종류%d (태그 %u 자리 %u 세대 %u)\n",
      frame / 23.81, co, uo, kind, tag, tag & 0x1fff, tag >> 13);
}
static void bwdump_selown_report() {
  if (!getenv("BWDUMP_SELOWN")) return;
  fprintf(stderr, "\n고른 유닛의 임자가 어긋난 비율(분마다)\n");
  for (int b = 0; b < 64; ++b) if (g_so_tot[b] >= 5)
    fprintf(stderr, "  %2d분  %5d건 중 %4d건 (%.1f%%)\n", b, g_so_tot[b], g_so_bad[b],
      g_so_bad[b] * 100.0 / g_so_tot[b]);
}
static void bwdump_aim_report() {
  if (!getenv("BWDUMP_AIM")) return;
  fprintf(stderr, "\n겨냥 어긋남(픽셀) — 리플레이가 적은 표적 좌표 vs 우리 시뮬의 그 유닛 자리\n");
  fprintf(stderr, "  분    건수   중앙   90%%    최대\n");
  for (int b = 0; b < 64; ++b) {
    if (g_aim[b].size() < 5) continue;
    std::vector<int> v = g_aim[b];
    std::sort(v.begin(), v.end());
    fprintf(stderr, "  %2d %7zu %6d %6d %7d\n", b, v.size(), v[v.size()/2],
      v[(size_t)(v.size()*0.9)], v.back());
  }
}
static int g_ord_first[256], g_ord_cnt[256];
void bwdump_ord(int id, int frame) {
  if (id < 0 || id >= 256) return;
  if (!g_ord_cnt[id]) g_ord_first[id] = frame;
  g_ord_cnt[id] += 1;
}
static void bwdump_ord_report() {
  if (!getenv("BWDUMP_ORD")) return;
  fprintf(stderr, "\n명령(order) 갈래마다 처음 쓰인 시각 — 늦은 차례 14개\n");
  int order[256], n = 0;
  for (int i = 0; i < 256; ++i) if (g_ord_cnt[i]) order[n++] = i;
  for (int a = 0; a < n; ++a) for (int b = a+1; b < n; ++b)
    if (g_ord_first[order[b]] > g_ord_first[order[a]]) { int t=order[a]; order[a]=order[b]; order[b]=t; }
  for (int i = 0; i < n && i < 24; ++i)
    fprintf(stderr, "  order %3d  처음 %6.2f분 · %d회\n", order[i], g_ord_first[order[i]]/23.81/60.0, g_ord_cnt[order[i]]);
}
static int g_rng_first[64], g_rng_cnt[64];
void bwdump_rng(int source, int frame) {
  if (source < 0 || source >= 64) return;
  if (!g_rng_cnt[source]) g_rng_first[source] = frame;
  g_rng_cnt[source] += 1;
}
static void bwdump_rng_report() {
  if (!getenv("BWDUMP_RNG")) return;
  fprintf(stderr, "\n난수 갈래마다 처음 쓰인 시각\n");
  int order[64], n = 0;
  for (int i = 0; i < 64; ++i) if (g_rng_cnt[i]) order[n++] = i;
  for (int a = 0; a < n; ++a) for (int b = a+1; b < n; ++b)
    if (g_rng_first[order[b]] < g_rng_first[order[a]]) { int t=order[a]; order[a]=order[b]; order[b]=t; }
  for (int i = 0; i < n; ++i)
    fprintf(stderr, "  갈래 %2d  처음 %6.2f분 · %d회\n", order[i], g_rng_first[order[i]]/23.81/60.0, g_rng_cnt[order[i]]);
}
static int g_shown = 0;
static int g_firstmiss = -1;
void bwdump_resolve(int frame, bool ok, bool slot, unsigned raw) {
  /* 태그 0은 **표적이 없다**는 뜻이다(빈 땅 우클릭) — 실패가 아니라 정상이다.
     이걸 실패로 세면 적중률이 통째로 낮게 나온다(처음에 56%로 보였던 것이 그것이다). */
  if (raw == 0) return;
  int b = frame / 1429; if (b > 63) b = 63;
  g_tot[b] += 1; if (ok) g_ok[b] += 1; if (slot) g_slot[b] += 1;
  if (bwdump_cur_owner >= 0 && bwdump_cur_owner < 12) { g_pt[bwdump_cur_owner][b] += 1; if (ok) g_po[bwdump_cur_owner][b] += 1; }
  if (!ok && g_firstmiss < 0) g_firstmiss = frame;
  if (!ok && getenv("BWDUMP_MISSTAGS")) fprintf(stderr, "MISS\t%u\t%d\n", raw, frame);
  if (!ok && slot && getenv("BWDUMP_FIRSTMISS") && g_shown < 14) {
    g_shown += 1;
    fprintf(stderr, "  세대만 틀림 · 프레임 %d (%.1f초) · 자리 %u · 리플레이 세대 %u\n",
      frame, frame / 23.81, (raw & 0x1fff), raw >> 13);
  }
}
bw_limits_t bw_limits;      /* 그릇 한도 — 요즘 리플레이면 아래에서 리마스터 값으로 올린다 */
static bool g_modern = false;
/* 리플레이가 명령에서 유닛을 가리킬 때 쓰는 수와 **같은 꼴**로 낸다.
   옛 판은 16비트 (자리+1) | (세대%32 << 11), 리마스터는 32비트 (자리+1) | (세대 << 13).
   꼴이 다르면 참값과 우리 분석을 태그로 짝지을 수가 없다. */
static unsigned bwdump_tag(const bwgame::unit_t* u) {
  return g_modern
    ? (unsigned)((u->index + 1) | ((u->unit_id_generation % (1u << 19)) << 13))
    : (unsigned)((u->index + 1) | ((u->unit_id_generation % (1u << 5)) << 11));
}

/* 트랙을 조밀한 이진으로 낸다 ─────────────────────────────────────────────────
   글자(TSV)로 내면 26분짜리 8인전이 39MB다. 서버가 트랙 하나에 쓸 수 있는 자리는 4MB라
   그대로는 못 싣는다. 그래서 이렇게 접는다(늘어놓으면 2.2MB가 된다).

     전체 = zlib( 아래 바이트열 )              ← 작은 끝(little-endian)

     머리
       char[4] "OBWT" · u8 판(=2) · f32 초당프레임 · i32 믿을프레임(-1이면 끝까지)
     로스터
       u8 사람수, 사람마다:
         u8 임자(0~11) · u8 리플레이id · u8 종족 · u8 편(force) · u8 controller
         · u32 개인색 · u8 이름길이 · 이름 바이트(UTF-8)
     트랙표
       u32 트랙수, 트랙마다:
         u32 태그 · u8 임자 · u16 유닛종류 · u32 키수 · u32 체력키수 · u32 인터셉터키수
     키 흐름 (트랙 차례대로, 트랙마다 앞 키와의 **차이**를 적는다)
       트랙마다 앞프레임=앞x=앞y=0에서 시작
       키마다: varint(zigzag(프레임차)) · varint(zigzag(x차)) · varint(zigzag(y차))
               · u8 방향(0~255) · u8 상태 · varint(zigzag(종류차))
       ★ 종류가 키마다 실린다 — 한 태그의 한 생애 안에서 종류가 **바뀐다**. 라바가
         알이 되고 저글링이 되고, 시즈탱크가 시즈모드가 되고, 저글링이 파묻힌다. 트랙에
         하나만 실으면 라바 시절도 저글링으로 그려진다. 안 바뀌는 동안은 0이라 거의
         공짜다(눌리면 사라진다).
       varint는 7비트씩 끊어 담고 더 있으면 최상위 비트를 세운다.
       zigzag는 (v << 1) ^ (v >> 31) — 음수도 작은 수로 만든다.
     체력 흐름 → 인터셉터 흐름 (트랙 차례대로, 키가 있는 트랙만)
       키마다: varint(프레임차) · varint(값차)
     업그레이드  u32 개수, 개마다 varint(프레임차) · u16 id · u8 단계 · u8 사람
                 id는 업그레이드 번호 그대로, 기술은 0x8000을 얹는다
     마법        u32 개수, 개마다 varint(프레임차) · u16 x · u16 y · u8 기술 · u8 사람
     핑          u32 개수, 개마다 varint(프레임차) · u16 x · u16 y · u8 사람

   x·y는 픽셀, 프레임은 그대로다. 읽는 쪽이 초·타일·도로 바꾼다.

   ★ 순서가 곧 규약이다. 여기를 고치면 src/utils/openbwTracks.ts를 같이 고쳐야 하고,
     scripts/openbw-tracks-check.mjs가 그 둘이 어긋나면 잡아 준다. */
/* ── 브라우저가 유추하던 몫을 여기서 낸다 ────────────────────────────────────────
   재생 화면은 자리·방향·상태 말고도 다섯을 더 쓴다: 로스터(누가 무슨 종족·무슨 색),
   체력, 인터셉터 수, 업그레이드·기술, 마법, 핑. 여태는 이걸 브라우저가 리플레이 명령에서
   **유추**했다 — 명령만 보고 "지금 이 유닛 체력이 얼마쯤일 것이다"를 셈하는 식이라,
   틀려도 틀린 줄을 알 길이 없었다.

   넷은 게임 상태에 그대로 들어 있다(체력·인터셉터·업그레이드·마법). 핑 하나만 게임
   상태가 아니라 명령인데, 덤퍼는 명령 스트림을 어차피 통째로 지나가므로 그 자리에서
   적으면 된다(actions.h의 read_action_ping_minimap).

   이것들이 다 나오면 브라우저가 리플레이를 파싱할 이유가 하나도 안 남는다. */
struct roster_t { int owner, pid, race, force, controller; unsigned color; std::string name; };
struct up_ev_t { int frame, id, level, player; };    /* id는 업그레이드 그대로, 기술은 0x8000|id */
struct cast_ev_t { int frame, x, y, tech, player; };
struct ping_ev_t { int frame, x, y, player; };
static std::vector<roster_t> g_roster;
static std::vector<up_ev_t> g_ups;
static std::vector<cast_ev_t> g_casts;
static std::vector<ping_ev_t> g_pings;
/* 상한 — 어긋난 판이 끝없이 쌓는 것을 막는다. 실측은 핑 38회·마법 수백 회 규모다. */
void bwdump_ping(int frame, int owner, int x, int y) {
  if (g_pings.size() < 20000) g_pings.push_back({frame, x, y, owner});
}
void bwdump_cast(int frame, int owner, int x, int y, int tech) {
  if (g_casts.size() < 100000) g_casts.push_back({frame, x, y, tech, owner});
}

struct track_key_t { int frame, x, y, head, state, type, owner; };

static void put_u8(std::vector<uint8_t>& b, unsigned v) { b.push_back((uint8_t)(v & 0xff)); }
static void put_u16(std::vector<uint8_t>& b, unsigned v) { put_u8(b, v); put_u8(b, v >> 8); }
static void put_u32(std::vector<uint8_t>& b, unsigned v) { put_u16(b, v); put_u16(b, v >> 16); }
static void put_varint(std::vector<uint8_t>& b, int v) {
  unsigned z = ((unsigned)v << 1) ^ (unsigned)(v >> 31);
  for (;;) { uint8_t c = z & 0x7f; z >>= 7; if (z) b.push_back(c | 0x80); else { b.push_back(c); break; } }
}

typedef std::map<unsigned, std::vector<std::pair<int,int>>> tick_store_t;  /* 태그 → [프레임, 값] */

static void bwdump_write_binary(const std::map<unsigned, std::vector<track_key_t>>& store,
    const tick_store_t& hp_store, const tick_store_t& ic_store, int trust_frame) {
  std::vector<uint8_t> b;
  b.push_back('O'); b.push_back('B'); b.push_back('W'); b.push_back('T');
  put_u8(b, 2);
  { const float fps = 23.81f; uint32_t bits; std::memcpy(&bits, &fps, 4); put_u32(b, bits); }
  put_u32(b, (unsigned)trust_frame);
  /* 로스터 — 임자 번호(0~11)와 리플레이가 적어 둔 사람 정보. 이름은 UTF-8이다
     (한글 아이디는 CP949로 들어 있어 replay.h와 같은 자로 옮긴다). */
  put_u8(b, (unsigned)g_roster.size());
  for (const auto& r : g_roster) {
    put_u8(b, (unsigned)r.owner); put_u8(b, (unsigned)r.pid);
    put_u8(b, (unsigned)r.race); put_u8(b, (unsigned)r.force);
    put_u8(b, (unsigned)r.controller); put_u32(b, r.color);
    const std::string nm = r.name.size() > 255 ? r.name.substr(0, 255) : r.name;
    put_u8(b, (unsigned)nm.size());
    for (unsigned char c : nm) b.push_back(c);
  }
  put_u32(b, (unsigned)store.size());
  for (const auto& kv : store) {
    /* 임자·종류는 **마지막에 무엇이었나**로 잡는다 — 라바가 알이 되고 저글링이 되는 것이
       한 태그의 한 생애다. 사라짐(3)은 종류를 안 바꾸므로 건너뛴다. */
    int owner = kv.second.empty() ? 0 : kv.second.front().owner;
    int type = kv.second.empty() ? 0 : kv.second.front().type;
    for (const auto& k : kv.second) if (k.state != 3) { owner = k.owner; type = k.type; }
    auto hit = hp_store.find(kv.first);
    auto iit = ic_store.find(kv.first);
    put_u32(b, kv.first); put_u8(b, (unsigned)owner);
    put_u16(b, (unsigned)type); put_u32(b, (unsigned)kv.second.size());
    put_u32(b, (unsigned)(hit == hp_store.end() ? 0 : hit->second.size()));
    put_u32(b, (unsigned)(iit == ic_store.end() ? 0 : iit->second.size()));
  }
  for (const auto& kv : store) {
    int pf = 0, px = 0, py = 0, pt = 0;
    for (const auto& k : kv.second) {
      put_varint(b, k.frame - pf); put_varint(b, k.x - px); put_varint(b, k.y - py);
      put_u8(b, (unsigned)k.head); put_u8(b, (unsigned)k.state);
      put_varint(b, k.type - pt);
      pf = k.frame; px = k.x; py = k.y; pt = k.type;
    }
  }
  /* 체력·인터셉터는 자리 키와 **따로** 간다. 섞으면 한쪽이 바뀔 때마다 다른 쪽 키까지
     끌려 나와 키 수가 몇 배가 된다 — 체력은 맞을 때마다 바뀌고 자리는 걸을 때마다
     바뀌는데, 그 둘은 같이 안 일어난다. */
  auto put_ticks = [&](const tick_store_t& ts) {
    for (const auto& kv : store) {
      auto it = ts.find(kv.first);
      if (it == ts.end()) continue;
      int pf = 0, pv = 0;
      for (const auto& t : it->second) {
        put_varint(b, t.first - pf); put_varint(b, t.second - pv);
        pf = t.first; pv = t.second;
      }
    }
  };
  put_ticks(hp_store);
  put_ticks(ic_store);
  /* 판 전체에 하나씩인 것들 — 업그레이드·마법·핑. 프레임은 차이로 적는다(차례대로다). */
  put_u32(b, (unsigned)g_ups.size());
  { int pf = 0; for (const auto& e : g_ups) {
      put_varint(b, e.frame - pf); put_u16(b, (unsigned)e.id);
      put_u8(b, (unsigned)e.level); put_u8(b, (unsigned)e.player); pf = e.frame; } }
  put_u32(b, (unsigned)g_casts.size());
  { int pf = 0; for (const auto& e : g_casts) {
      put_varint(b, e.frame - pf); put_u16(b, (unsigned)e.x); put_u16(b, (unsigned)e.y);
      put_u8(b, (unsigned)e.tech); put_u8(b, (unsigned)e.player); pf = e.frame; } }
  put_u32(b, (unsigned)g_pings.size());
  { int pf = 0; for (const auto& e : g_pings) {
      put_varint(b, e.frame - pf); put_u16(b, (unsigned)e.x); put_u16(b, (unsigned)e.y);
      put_u8(b, (unsigned)e.player); pf = e.frame; } }
  uLongf out_len = compressBound((uLong)b.size());
  std::vector<uint8_t> out(out_len);
  if (compress2(out.data(), &out_len, b.data(), (uLong)b.size(), 9) != Z_OK)
    { fprintf(stderr, "트랙을 누르지 못했다\n"); exit(1); }
  fwrite(out.data(), 1, out_len, stdout);
  size_t hpn = 0, icn = 0;
  for (const auto& kv : hp_store) hpn += kv.second.size();
  for (const auto& kv : ic_store) icn += kv.second.size();
  fprintf(stderr, "이진 트랙 — 트랙 %zu개 · 체력 %zu · 인터셉터 %zu · 업그레이드 %zu · 마법 %zu · 핑 %zu\n",
    store.size(), hpn, icn, g_ups.size(), g_casts.size(), g_pings.size());
  fprintf(stderr, "  편 %.1fMB → 눌러서 %.1fMB\n", b.size() / 1048576.0, out_len / 1048576.0);
}

using namespace bwgame;

int main(int argc, char** argv) {
  if (argc < 3) { fprintf(stderr, "쓰기: bwdump <자료폴더> <리플레이.rep> [간격]\n"); return 2; }
  std::string dir = argv[1];
  const int step = argc > 3 ? atoi(argv[3]) : 24;

  auto load_file = [&](a_vector<uint8_t>& dst, a_string filename) {
    /* 이름 안의 역슬래시를 슬래시로 — images.tbl이 적어 둔 그림 이름은 "zerg\\avenger.grp"
       처럼 윈도 표기라, 그대로 이으면 맥·리눅스에서 파일을 못 찾는다. */
    std::string p = dir + "/" + filename.c_str();
    for (char& c : p) if (c == '\\') c = '/';
    FILE* f = fopen(p.c_str(), "rb");
    if (!f) error("자료 파일 없음: %s", p.c_str());
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    dst.resize((size_t)n);
    if (fread(dst.data(), 1, (size_t)n, f) != (size_t)n) error("읽기 실패: %s", p.c_str());
    fclose(f);
  };

  /* 자료를 어디서 읽나 — 둘 다 받는다.
     ① 폴더에 MPQ 셋(Patch_rt·BrooDat·StarDat)이 있으면 그대로 읽는다. 스타크래프트
        설치 폴더를 그냥 가리키면 되는 길이다.
     ② 없으면 풀어 놓은 파일 열 개를 읽는다. 배포 서버에는 이쪽이 낫다 — 그림·소리가
        든 통짜 MPQ(수백 MB)를 안 두고, 시뮬에 진짜 필요한 2MB 남짓만 둔다. */
  const bool have_mpq = [&]{
    FILE* f = fopen((dir + "/StarDat.mpq").c_str(), "rb");
    if (!f) f = fopen((dir + "/stardat.mpq").c_str(), "rb");
    if (!f) return false;
    fclose(f); return true;
  }();
  fprintf(stderr, "자료: %s (%s)\n", dir.c_str(), have_mpq ? "MPQ" : "풀어 놓은 파일");
  game_player player;
  if (have_mpq) player.init(data_loading::data_files_directory(a_string(dir.c_str())));
  else player.init(load_file);
  action_state action_st;
  replay_state replay_st;
  replay_functions rf(player.st(), action_st, replay_st);
  /* 옛 형식이면 OpenBW의 읽개로, 리마스터(1.21+)면 우리 읽개로 — 표식과 압축이 달라
     한쪽 읽개로는 다른 쪽을 못 읽는다(modern_replay.h 머리말). */
  if (data_loading::is_modern_replay(argv[2])) {
    g_modern = true;
    fprintf(stderr, "리플레이 형식: 리마스터(1.21+)\n");
    /* 리마스터는 그릇 한도를 두 배로 늘렸다 — 리플레이의 LMTS 구획에 적힌 값이 이것이다.
       유닛 자리 번호가 `한도 − 몇째로 만들어졌나`라서, 1700칸으로 두면 번호가 통째로
       1700씩 어긋나 명령이 유닛을 못 찾는다. */
    bw_limits.units = 3400; bw_limits.bullets = 400; bw_limits.sprites = 5000;
    bw_limits.images = 10000; bw_limits.orders = 4000; bw_limits.thingies = 1000;
    /* 하나씩 되돌려 보기(시험용) */
    if (getenv("BWLIM_BULLETS")) bw_limits.bullets = (size_t)atoi(getenv("BWLIM_BULLETS"));
    if (getenv("BWLIM_SPRITES")) bw_limits.sprites = (size_t)atoi(getenv("BWLIM_SPRITES"));
    if (getenv("BWLIM_IMAGES")) bw_limits.images = (size_t)atoi(getenv("BWLIM_IMAGES"));
    if (getenv("BWLIM_ORDERS")) bw_limits.orders = (size_t)atoi(getenv("BWLIM_ORDERS"));
    if (getenv("BWLIM_THINGIES")) bw_limits.thingies = (size_t)atoi(getenv("BWLIM_THINGIES"));
    fprintf(stderr, "그릇 한도: 유닛 %zu · 스프라이트 %zu · 이미지 %zu\n",
      bw_limits.units, bw_limits.sprites, bw_limits.images);
    auto file_r = data_loading::file_reader<>(a_string(argv[2]));
    /* 지도(.chk)를 옆으로 빼 둔다 — BWDUMP_CHK에 경로를 주면 그 파일로 쓴다.
       지도가 선언한 유닛 수와 시뮬이 실제로 세운 수를 견주는 데 쓴다. */
    std::vector<uint8_t> chk;
    rf.load_replay(data_loading::make_modern_replay_file_reader(file_r), true,
                   getenv("BWDUMP_CHK") ? &chk : nullptr);
    if (getenv("BWDUMP_CHK") && !chk.empty()) {
      FILE* c = fopen(getenv("BWDUMP_CHK"), "wb");
      if (c) { fwrite(chk.data(), 1, chk.size(), c); fclose(c);
        fprintf(stderr, "지도 %zu 바이트를 %s 로 뺐다\n", chk.size(), getenv("BWDUMP_CHK")); }
    }
  } else {
    fprintf(stderr, "리플레이 형식: 옛것\n");
    std::vector<uint8_t> chk;
    auto file_r = data_loading::file_reader<>(a_string(argv[2]));
    rf.load_replay(data_loading::make_replay_file_reader(file_r), true,
                   getenv("BWDUMP_CHK") ? &chk : nullptr);
    if (getenv("BWDUMP_CHK") && !chk.empty()) {
      FILE* c = fopen(getenv("BWDUMP_CHK"), "wb");
      if (c) { fwrite(chk.data(), 1, chk.size(), c); fclose(c); }
    }
  }

  state& st = player.st();
  fprintf(stderr, "맵 %dx%d · 끝 프레임 %d · 이름 %s\n",
    (int)st.game->map_width, (int)st.game->map_height, (int)replay_st.end_frame,
    replay_st.map_name.c_str());
  for (size_t i = 0; i != 12; ++i) {
    if (!replay_st.player_name[i].empty())
      fprintf(stderr, "  자리 %zu: %s · 미네랄 %d · 가스 %d\n", i,
        replay_st.player_name[i].c_str(), (int)st.current_minerals[i], (int)st.current_gas[i]);
    if (getenv("BWDUMP_SLOTS"))
      fprintf(stderr, "     └ 명령 임자번호 %d · 종족 %d · 조종 %d\n",
        (int)action_st.player_id[i], (int)st.players[i].race, (int)st.players[i].controller);
  }

  if (getenv("BWDUMP_IDX")) {
    size_t n = 0, mn = (size_t)-1, mx = 0;
    for (unit_t* u : ptr(st.visible_units)) {
      n += 1; mn = std::min(mn, (size_t)u->index); mx = std::max(mx, (size_t)u->index);
    }
    fprintf(stderr, "시작 유닛 %zu기 · index %zu~%zu · 첫 유닛 id32 %u\n", n, mn, mx,
      st.visible_units.empty() ? 0u : (unsigned)rf.get_unit_id_32(&*st.visible_units.begin()).raw_value);
  }
  if (getenv("BWDUMP_CMDS")) {
    const auto& b = replay_st.actions_data_buffer;
    size_t p = 0; int shown = 0;
    while (p + 5 <= b.size() && shown < 14) {
      int frame = (int)(b[p] | (b[p+1]<<8) | (b[p+2]<<16) | ((unsigned)b[p+3]<<24));
      size_t len = b[p+4];
      fprintf(stderr, "프레임 %6d · %2zu바이트:", frame, len);
      for (size_t i = 0; i < len && p+5+i < b.size(); ++i) fprintf(stderr, " %02x", b[p+5+i]);
      fprintf(stderr, "\n");
      p += 5 + len; shown += 1;
    }
  }
  /* 태그(tag) 칸이 대조의 열쇠다 — 리플레이 명령이 유닛을 가리킬 때 쓰는 바로 그 수라,
     우리 분석이 붙인 개체와 **한 자리도 안 틀리고** 짝지을 수 있다. 리마스터 규약은
     (index + 1 + 1700) | (generation << 13) 이다(actions.h의 get_unit_scr 머리말). */
  /* --units: 프레임마다 전부 뱉는 대신 **유닛 생애표**만 낸다. 태그마다 한 줄로
     [정체·임자·태어난 프레임·마지막으로 보인 프레임·죽었나·첫 자리·끝 자리]다.
     대조(scripts/truth-check.mjs)에는 이게 자리마다의 좌표보다 훨씬 쓸모 있고, 매 프레임을
     빠짐없이 훑으므로 **한 프레임만 살다 간 유닛도 안 놓친다**. */
  bool units_mode = false;
  bool tracks_mode = false;
  bool bin_mode = false;
  for (int i = 3; i < argc; ++i) {
    if (std::string(argv[i]) == "--units") units_mode = true;
    if (std::string(argv[i]) == "--tracks") tracks_mode = true;
    if (std::string(argv[i]) == "--bin") bin_mode = true;
  }
  /* 컴퓨터 플레이어가 끼어 있으면 이 판은 원리상 못 돌린다 — OpenBW에는 컴퓨터 AI가
     아예 없다(AIPatrol·ComputerAI 같은 명령이 구현되어 있지 않다). 참값을 내 봐야
     쓰레기이므로 먼저 크게 알린다. */
  {
    int comps = 0;
    if (getenv("BWDUMP_CTRL")) for (size_t i = 0; i != 12; ++i) fprintf(stderr, "  자리 %zu controller=%d race=%d\n", i, (int)player.st().players[i].controller, (int)player.st().players[i].race);
    for (size_t i = 0; i != 12; ++i) {
      auto c = player.st().players[i].controller;
      if (c == player_t::controller_computer || c == player_t::controller_computer_game) comps += 1;
    }
    if (comps) {
      fprintf(stderr, "\n⚠⚠ 컴퓨터 플레이어 %d명 — OpenBW에는 컴퓨터 AI가 없다. 이 판의 참값은 쓸 수 없다.\n\n", comps);
      g_no_ai = true;
    }
  }
  struct life_t { int kind, owner, born, last; int bx, by, lx, ly; };
  std::map<unsigned, life_t> lives;
  if (units_mode) {
    while ((int)st.current_frame < (int)replay_st.end_frame) {
      rf.next_frame();
      /* 보이는 것 + **숨은 것**(수송선 안·건물 안)까지 훑는다 — 실려 있는 동안은
         visible_units에서 빠지므로, 그것만 보면 드랍된 유닛이 통째로 안 잡힌다. */
      if (getenv("BWDUMP_ACC")) {
        /* 무엇이 쌓이나 — 분마다 누적 난수 뽑기·유닛 만들기·죽음·총알을 찍는다.
           판마다 갈리는 시각의 값이 비슷하면 '뽑기 한 번마다 어긋날 확률'이 있다는 뜻이다. */
        static int lastb = -1;
        int b = (int)st.current_frame / 1429;
        if (b != lastb) {
          lastb = b;
          fprintf(stderr, "ACC\t%d\t%llu\n", b, (unsigned long long)st.total_random_counts);
        }
      }
      if (getenv("BWDUMP_PEAK")) {
        static size_t pu = 0, pb = 0, ps = 0, pi = 0, po = 0, pp = 0, pt = 0;
        auto up = [](size_t& m, size_t v) { if (v > m) m = v; };
        auto used = [](auto& c) { size_t f = 0; for (auto& v : c.free_list) { (void)v; ++f; } return c.size - f; };
        up(pu, used(st.units_container));
        up(pb, (size_t)st.active_bullets_size);
        up(ps, used(st.sprites_container));
        up(pi, used(st.images_container));
        up(po, (size_t)st.active_orders_size);
        up(pp, st.paths.size());
        up(pt, st.thingies.size());
        if ((int)st.current_frame + 1 >= (int)replay_st.end_frame)
          fprintf(stderr, "그릇 최고치 — 유닛 %zu/%zu · 총알 %zu/%zu · 스프라이트 %zu/%zu · 이미지 %zu/%zu"
            " · 명령 %zu/%zu · 경로 %zu/1024 · thingy %zu/%zu\n",
            pu, bw_limits.units, pb, bw_limits.bullets, ps, bw_limits.sprites,
            pi, bw_limits.images, po, bw_limits.orders, pp, pt, bw_limits.thingies);
      }
      std::vector<unit_t*> all9;
      for (unit_t* u : ptr(st.visible_units)) all9.push_back(u);
      for (unit_t* u : ptr(st.hidden_units)) all9.push_back(u);
      /* 포탑(subunit)은 **어느 목록에도 안 들어간다** — 시즈탱크·골리앗 같은 유닛은 포탑이
         별도의 자리와 세대를 먹는데, OpenBW의 create_unit은 본체만 목록에 넣는다. 빼먹으면
         명부에 세대가 통째로 비어 보이고, 리플레이가 그 자리를 가리킬 때 영문을 알 수 없다. */
      { size_t n9 = all9.size();
        for (size_t i9 = 0; i9 < n9; ++i9) if (all9[i9]->subunit) all9.push_back(all9[i9]->subunit); }
      for (unit_t* u : all9) {
        if (getenv("BWDUMP_ORD")) bwdump_ord((int)u->order_type->id, (int)st.current_frame);
        const unsigned tg = bwdump_tag(u);
        auto it = lives.find(tg);
        if (it == lives.end()) {
          lives.emplace(tg, life_t{ (int)u->unit_type->id, (int)u->owner,
            (int)st.current_frame, (int)st.current_frame,
            u->position.x, u->position.y, u->position.x, u->position.y });
        } else {
          it->second.last = (int)st.current_frame;
          it->second.lx = u->position.x;
          it->second.ly = u->position.y;
        }
      }
    }
    {
      const int tf = bwdump_trust_frame();
      printf("#trust\t%d\n", tf);
      if (tf >= 0)
        fprintf(stderr, "⚠ 참값을 믿을 수 있는 구간: 0 ~ %.1f분 (그 뒤로 시뮬이 실제 게임과 갈라진다)\n", tf / 23.81 / 60.0);
      else fprintf(stderr, "참값은 끝까지 믿을 수 있다\n");
    }
    printf("tag\tkind\towner\tborn\tlast\tdied\tbx\tby\tlx\tly\n");
    for (const auto& kv : lives) {
      const auto& L = kv.second;
      printf("%u\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n", kv.first, L.kind, L.owner,
        L.born, L.last, L.last < (int)replay_st.end_frame - 1 ? 1 : 0, L.bx, L.by, L.lx, L.ly);
    }
    fprintf(stderr, "유닛 %zu기의 생애를 냈다\n", lives.size());
    if (getenv("BWDUMP_RESOLVE")) {
      fprintf(stderr, "  명령 갈래마다 처음 나온 시각:\n");
      {
        int order[256]; int n = 0;
        for (int i = 0; i < 256; ++i) if (g_cnt[i]) order[n++] = i;
        for (int a = 0; a < n; ++a) for (int b = a + 1; b < n; ++b)
          if (g_first[order[b]] < g_first[order[a]]) { int t = order[a]; order[a] = order[b]; order[b] = t; }
        for (int i = 0; i < n; ++i)
          fprintf(stderr, "    %3d(0x%02x)  처음 %6d프레임(%5.1f초) · %d회\n",
            order[i], order[i], g_first[order[i]], g_first[order[i]] / 23.81, g_cnt[order[i]]);
      }
      fprintf(stderr, "  분  고르기  찾음   슬롯만\n");
      for (int b = 0; b < 64; ++b) if (g_tot[b])
        fprintf(stderr, "  %2d  %6d  %5.1f%%  %5.1f%%\n", b, g_tot[b],
          g_ok[b] * 100.0 / g_tot[b], g_slot[b] * 100.0 / g_tot[b]);
    }
    bwdump_gen_report();
    bwdump_why_report();
    bwdump_rng_report();
    bwdump_ord_report();
    bwdump_aim_report();
    bwdump_selown_report();
    bwdump_ai_report();
    bwdump_trigmiss_report();
    bwdump_fail_report();
    bwdump_trig_report();
    bwdump_owner_time_report();
    {
      int o = 0, t = 0;
      for (int b = 0; b < 64; ++b) { o += g_ok[b]; t += g_tot[b]; }
      fprintf(stderr, "요약\t첫어긋남 %.1f초\t전체적중 %.1f%%\t유닛 %zu기\n",
        g_firstmiss < 0 ? 9999.0 : g_firstmiss / 23.81, t ? o * 100.0 / t : 0.0, lives.size());
    }
    for (size_t i = 0; i != 8; ++i) {
      if (replay_st.player_name[i].empty()) continue;
      fprintf(stderr, "  %-16s 끝 자원 미네랄 %6d · 가스 %5d · 캔 미네랄 %7d\n",
        replay_st.player_name[i].c_str(), (int)st.current_minerals[i], (int)st.current_gas[i],
        (int)st.total_minerals_gathered[i]);
    }
    return 0;
  }
  if (tracks_mode) {
    /* 앱이 먹는 트랙 꼴 — 키프레임 [t, x, y, 방향, 상태]에 필요한 것만 낸다.
       상태 번호는 앱의 것과 같다: 0 가만 1 이동 2 안에 탐 3 사라짐 4 싸움 5 채취
       6 파묻힘 7 미네랄 들고 8 가스 들고.

       매 표본을 그대로 적으면 14분짜리가 240만 줄이다. **곧게 가는 동안은 안 적는다** —
       직전 표본이 '적은 점 → 지금'을 잇는 선에서 얼마나 벗어나는지 보고, 벗어날 때만
       그 직전 표본을 꺾임점으로 남긴다. 읽는 쪽은 키 사이를 곧게 이어 그린다. */
    if (!bin_mode) printf("frame\ttag\towner\ttype\tx\ty\thead\tstate\n");
    using key_t = track_key_t;
    long why9[6] = {0,0,0,0,0,0};
    /* --bin이면 글자로 안 쓰고 모아 두었다가 끝에 한 번에 눌러서 낸다(아래 참고).
       26분짜리 8인전이 글자로는 39MB인데 눌러서 2.2MB가 된다 — 서버 상한이 4MB다. */
    std::map<unsigned, std::vector<key_t>> store9;
    std::set<unsigned> skipres9;   /* 지도가 놓아 준 자원 — 앱이 지도에서 그린다 */
    auto is_map_resource = [](int t) {
      return t == 176 || t == 177 || t == 178 || t == 188 || t == 214;
    };
    auto emit9 = [&](unsigned tg, const key_t& c) {
      if (skipres9.count(tg)) return;
      if (bin_mode) { store9[tg].push_back(c); return; }
      printf("%d\t%u\t%d\t%d\t%d\t%d\t%d\t%d\n", c.frame, tg, c.owner,
        c.type, c.x, c.y, c.head, c.state);
    };
    /* 얼마나 곱게 남길지 — BWDUMP_DEV(픽셀, 기본 4) · BWDUMP_HEAD(0~255, 기본 28).
       키가 많을수록 곱지만 폰으로 보내는 짐이 무거워진다. */
    const double DEV9 = getenv("BWDUMP_DEV") ? atof(getenv("BWDUMP_DEV")) : 4.0;
    const int HEAD9 = getenv("BWDUMP_HEAD") ? atoi(getenv("BWDUMP_HEAD")) : 28;
    std::map<unsigned, key_t> anchor9;   /* 마지막으로 적은 키 */
    std::map<unsigned, key_t> pend9;     /* 아직 안 적은 직전 표본 */
    /* 로스터 — 리플레이 머리말이 아는 것을 그대로 옮긴다. 한글 아이디는 CP949로 들어
       있어서 replay.h가 지도 이름에 쓰는 것과 같은 자로 UTF-8로 옮긴다. */
    for (int pi = 0; pi < 12; ++pi) {
      const int ctl = st.players[pi].controller;
      if (ctl != player_t::controller_occupied && ctl != player_t::controller_computer_game
          && ctl != player_t::controller_computer && ctl != player_t::controller_user_left) continue;
      a_string nm = replay_st.player_name[pi], kn;
      if (korean::korean_locale_to_utf8(nm, kn)) nm = kn;
      g_roster.push_back({ pi, (int)action_st.player_id[pi], (int)st.players[pi].race,
        st.players[pi].force, ctl, (unsigned)st.players[pi].color, std::string(nm.c_str()) });
    }
    /* 체력(실드 포함)과 인터셉터 수 — **바뀔 때만** 적는다. 안 바뀌는 동안은 한 줄도
       안 남으므로, 안 맞는 유닛은 태어날 때 한 번이 전부다. */
    tick_store_t hp9, ic9;
    std::map<unsigned, int> lasthp9, lastic9;
    /* 업그레이드·기술 — 상태를 표본마다 훑어 **달라진 것만** 적는다. 리플레이 명령에는
       '연구를 눌렀다'만 있고 언제 끝났는지가 없다(프로토스는 전력이 끊기면 멈춘다).
       여기서는 실제로 올라간 순간이 그대로 나온다. */
    static int lastup9[12][61] = {{0}};
    static bool lasttc9[12][44] = {{false}};
    while ((int)st.current_frame < (int)replay_st.end_frame) {
      rf.next_frame();
      if ((int)st.current_frame % step) continue;
      for (const auto& r : g_roster) {
        for (int ui = 0; ui < 61; ++ui) {
          const int lv = st.upgrade_levels[r.owner][(UpgradeTypes)ui];
          if (lv == lastup9[r.owner][ui]) continue;
          lastup9[r.owner][ui] = lv;
          g_ups.push_back({ (int)st.current_frame, ui, lv, r.owner });
        }
        for (int ti = 0; ti < 44; ++ti) {
          const bool got = st.tech_researched[r.owner][(TechTypes)ti];
          if (got == lasttc9[r.owner][ti]) continue;
          lasttc9[r.owner][ti] = got;
          if (got) g_ups.push_back({ (int)st.current_frame, 0x8000 | ti, 1, r.owner });
        }
      }
      std::vector<unit_t*> all9;
      std::set<unsigned> seen9;
      for (unit_t* u : ptr(st.visible_units)) all9.push_back(u);
      for (unit_t* u : ptr(st.hidden_units)) all9.push_back(u);
      /* 트랙에는 포탑을 안 넣는다 — 앱은 시즈탱크·골리앗을 하나로 그린다.
         (참값 명부(--units)에는 넣는다. 포탑도 자리와 세대를 먹기 때문이다.) */
      for (unit_t* u : all9) {
        /* 상태는 **떨리지 않는** 기준으로 잡는다. 재장전 시계나 속도로 잡으면 한 번 쏠
           때마다, 한 발짝 멈출 때마다 갈래가 바뀌어 키가 폭증한다(전체의 6할이 그것이었다).
           지금 무슨 명령을 받고 있나로 본다 — 싸우는 동안 죽 '싸움'이다. */
        const auto oid9 = u->order_type->id;
        const bool fighting9 = oid9 == Orders::AttackUnit || oid9 == Orders::AttackFixedRange
          || oid9 == Orders::AttackMove || oid9 == Orders::TowerAttack;
        int state9 = 0;
        if (rf.us_hidden(u)) state9 = 2;
        else if (rf.u_burrowed(u)) state9 = 6;
        else if (u->carrying_flags & 2) state9 = 7;
        else if (u->carrying_flags & 1) state9 = 8;
        else if (rf.u_gathering(u)) state9 = 5;
        else if (fighting9) state9 = 4;
        else if (rf.u_movement_flag(u, 2)) state9 = 1;
        const unsigned tg = bwdump_tag(u);
        if (!is_map_resource((int)u->unit_type->id)) {
          /* 실드를 더한 값이다 — 재생 화면의 체력바가 그렇게 그린다. 0은 오직 죽음의
             표시라 살아 있으면 최소 1로 올린다(반올림에 죽는 일이 없게). */
          int hpv = (u->hp.raw_value + u->shield_points.raw_value) / 256;
          if (hpv < 1) hpv = 1;
          auto lh = lasthp9.find(tg);
          /* 잔물결은 안 적는다 — 저그는 체력이, 프로토스는 실드가 **쉬는 내내** 1씩 차
             오른다. 그걸 다 적으면 체력 키가 자리 키만큼 불어나는데(60만 개), 화면에
             그려지는 것은 체력바 한 칸이라 눈에 보이지도 않는다. 최대치의 2%나 2점 중
             큰 쪽을 넘을 때만, 그리고 **다 찼거나 죽기 직전**은 놓치지 않게 적는다. */
          const int hpmax = (u->unit_type->hitpoints.raw_value
            + u->unit_type->shield_points * 256) / 256;
          int thr = hpmax / 50; if (thr < 2) thr = 2;
          const bool edge = hpv >= hpmax || hpv <= hpmax / 20;
          if (lh == lasthp9.end() || std::abs(lh->second - hpv) >= thr
              || (edge && lh->second != hpv)) {
            hp9[tg].push_back({ (int)st.current_frame, hpv }); lasthp9[tg] = hpv;
          }
          /* 인터셉터는 캐리어만 있다 — 그 자리는 유닛 종류마다 다른 것이 겹쳐 있는
             공용체라, 캐리어가 아닌데 읽으면 엉뚱한 수가 나온다. */
          if (u->unit_type->id == UnitTypes::Protoss_Carrier
              || u->unit_type->id == UnitTypes::Hero_Gantrithor) {
            const int icv = (int)(u->carrier.inside_count + u->carrier.outside_count);
            auto li = lastic9.find(tg);
            if (li == lastic9.end() || li->second != icv) {
              ic9[tg].push_back({ (int)st.current_frame, icv }); lastic9[tg] = icv;
            }
          }
        }
        const key_t cur{ (int)st.current_frame, u->position.x, u->position.y,
          (int)rf.direction_index(u->heading), state9, (int)u->unit_type->id, (int)u->owner };
        seen9.insert(tg);
        auto it = anchor9.find(tg);
        if (it == anchor9.end()) {
          if (is_map_resource(cur.type)) { skipres9.insert(tg); anchor9[tg] = cur; continue; }
          why9[0]++; emit9(tg, cur); anchor9[tg] = cur; continue;
        }
        const key_t anc = it->second;
        auto pit = pend9.find(tg);
        if (anc.state != cur.state || anc.type != cur.type || anc.owner != cur.owner) {
          why9[1]++;
          if (pit != pend9.end()) { why9[1]++; emit9(tg, pit->second); pend9.erase(pit); }
          emit9(tg, cur); anchor9[tg] = cur; continue;
        }
        if (pit != pend9.end()) {
          const key_t pv = pit->second;
          const double dx = (double)cur.x - anc.x, dy = (double)cur.y - anc.y;
          const double len = std::sqrt(dx * dx + dy * dy);
          double dev = len < 1e-6
            ? std::sqrt((double)(pv.x - anc.x) * (pv.x - anc.x) + (double)(pv.y - anc.y) * (pv.y - anc.y))
            : std::fabs((pv.x - anc.x) * dy - (pv.y - anc.y) * dx) / len;
          int hd = std::abs(pv.head - cur.head); if (hd > 128) hd = 256 - hd;
          /* 오래됐다고 찍는 것은 **움직이는 것**에만 — 건물과 미네랄까지 10초마다
             찍으면 그것만으로 키의 4분의 1이 된다. */
          const bool moving9 = cur.state == 1 || cur.state == 5 || cur.state == 7 || cur.state == 8;
          if (dev > DEV9 || hd > HEAD9 || (moving9 && pv.frame - anc.frame >= 240)) {
            why9[dev > DEV9 ? 2 : (hd > HEAD9 ? 3 : 4)]++;
            emit9(tg, pv); anchor9[tg] = pv; pend9[tg] = cur; continue;
          }
        }
        pend9[tg] = cur;
      }
      /* 이번에 안 보이면 사라진 것이다 — 마지막 표본을 적고 상태 3(사라짐)을 찍는다. */
      for (auto it = anchor9.begin(); it != anchor9.end();) {
        if (seen9.count(it->first)) { ++it; continue; }
        auto pit = pend9.find(it->first);
        if (pit != pend9.end()) { emit9(it->first, pit->second); pend9.erase(pit); }
        why9[5]++;
        key_t g = it->second; g.frame = (int)st.current_frame; g.state = 3;
        emit9(it->first, g);
        it = anchor9.erase(it);
      }
    }
    for (auto& kv : pend9) emit9(kv.first, kv.second);
    /* 믿을 수 있는 구간은 **다 돌고 나서야** 알 수 있다(고르기 적중률로 재므로).
       그래서 맨 뒤에 적는다 — 읽는 쪽은 줄을 다 훑으니 자리는 상관없다. */
    if (!bin_mode) {
      /* 글자 갈래에도 똑같이 낸다 — 자물쇠(scripts/openbw-tracks-check.mjs)가 이진과
         견주는 잣대다. 사람이 읽을 것은 아니라 꼴은 투박해도 된다. */
      for (const auto& r : g_roster)
        printf("#player\t%d\t%d\t%d\t%d\t%d\t%u\t%s\n",
          r.owner, r.pid, r.race, r.force, r.controller, r.color, r.name.c_str());
      for (const auto& kv : hp9) for (const auto& t : kv.second)
        printf("#hp\t%u\t%d\t%d\n", kv.first, t.first, t.second);
      for (const auto& kv : ic9) for (const auto& t : kv.second)
        printf("#ic\t%u\t%d\t%d\n", kv.first, t.first, t.second);
      for (const auto& e : g_ups)
        printf("#up\t%d\t%d\t%d\t%d\n", e.frame, e.id, e.level, e.player);
      for (const auto& e : g_casts)
        printf("#cast\t%d\t%d\t%d\t%d\t%d\n", e.frame, e.x, e.y, e.tech, e.player);
      for (const auto& e : g_pings)
        printf("#ping\t%d\t%d\t%d\t%d\n", e.frame, e.x, e.y, e.player);
      printf("#trust\t%d\n", bwdump_trust_frame());
    }
    if (bin_mode) bwdump_write_binary(store9, hp9, ic9, bwdump_trust_frame());
    {
      const int tf = bwdump_trust_frame();
      fprintf(stderr, "키가 나온 까닭 — 처음 %ld · 갈래바뀜 %ld · 길벗어남 %ld · 방향꺾임 %ld · 오래됨 %ld · 사라짐 %ld\n",
        why9[0], why9[1], why9[2], why9[3], why9[4], why9[5]);
      if (tf >= 0) fprintf(stderr, "⚠ 트랙을 믿을 수 있는 구간: 0 ~ %.1f분\n", tf / 23.81 / 60.0);
      else fprintf(stderr, "트랙은 끝까지 믿을 수 있다\n");
    }
    fprintf(stderr, "끝\n");
    return 0;
  }
  printf("frame\ttag\towner\ttype\tx\ty\thp\tshield\tenergy\tcompleted\n");
  while ((int)st.current_frame < (int)replay_st.end_frame) {
    rf.next_frame();
    if ((int)st.current_frame % step) continue;
    for (unit_t* u : ptr(st.visible_units)) {
      const unsigned scr_tag = bwdump_tag(u);
      printf("%d\t%u\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n",
        (int)st.current_frame, scr_tag, (int)u->owner,
        (int)u->unit_type->id, u->position.x, u->position.y,
        u->hp.raw_value / 256, u->shield_points.raw_value / 256,
        u->energy.raw_value / 256, rf.u_completed(u) ? 1 : 0);
    }
  }
  fprintf(stderr, "끝\n");
  return 0;
}
