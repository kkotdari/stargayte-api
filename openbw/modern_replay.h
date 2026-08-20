/* 리마스터(1.21+) 리플레이를 OpenBW에 먹이는 읽개 ────────────────────────────────
 *
 * OpenBW의 replay.h는 **옛 형식**만 읽는다: 안쪽 표식이 "reRS"(0x53526572)이고 토막마다
 * PKWARE explode로 풀린다. 그런데 지금 쓰이는 리플레이는 전부 리마스터 형식이라 표식이
 * "seRS"(0x53526573)이고 토막이 **zlib**으로 눌려 있다. 그래서 파일을 그대로 주면
 * "invalid identifier"에서 죽는다.
 *
 * 틀은 옛것과 같다(icza/screp의 repdecoder로 확인):
 *     [uint32 crc32][uint32 토막 수] 그다음 토막마다 [uint32 눌린 길이][그 바이트들]
 * 다른 것은 딱 둘이다.
 *   ① 눌렸는지 가리는 법 — 옛 형식은 "들어온 길이 == 나갈 길이면 안 눌린 것"으로 봤는데,
 *      리마스터는 zlib 머리(0x78, 그리고 앞 두 바이트가 31의 배수)로 가린다.
 *   ② 푸는 법 — explode가 아니라 zlib inflate다.
 * crc32는 확인하지 않는다(리마스터가 같은 방식으로 셈하지 않는다).
 */
#pragma once
#include "replay.h"
#include <zlib.h>
#include <vector>
#include <algorithm>

namespace bwgame {
namespace data_loading {

template<typename base_reader_T, bool default_little_endian = true>
struct modern_replay_file_reader {
	base_reader_T& r;
	explicit modern_replay_file_reader(base_reader_T& r) : r(r) {}

	void get_bytes(uint8_t* output, size_t output_size) {
		/* 1.21+에는 **첫 토막과 둘째 토막 사이에 4바이트가 하나 더** 있다(screp의
		   repdecoder가 sectionsCounter == 2에서 그것을 건너뛴다). 실측으로도 파일 머리는
		   [crc][조각 수][길이]["seRS"] 다음에 정체 모를 4바이트가 오고 그 뒤에 헤더 토막이
		   시작한다. 안 건너뛰면 그다음부터 통째로 어긋나 "끝을 넘어 읽음"으로 죽는다. */
		if (++framed_reads == 2) r.template get<uint32_t>();
		uint32_t crc = r.template get<uint32_t>();          // crc32 — 안 본다
		size_t segments = r.template get<uint32_t>();
		if (getenv("BWDUMP_DEBUG")) fprintf(stderr, "  [토막] 나갈 %zu · crc %08x · 조각 %zu\n", output_size, crc, segments);
		std::vector<uint8_t> in;
		size_t pos = 0;
		for (size_t i = 0; i != segments; ++i) {
			size_t in_size = r.template get<uint32_t>();
			size_t out_size = std::min<size_t>(8192, output_size - pos);
			in.resize(in_size);
			r.get_bytes(in.data(), in_size);
			const bool zipped = in_size > 4 && in[0] == 0x78
				&& ((((unsigned)in[0] << 8) | (unsigned)in[1]) % 31) == 0;
			if (zipped) {
				uLongf got = (uLongf)out_size;
				int rc = uncompress(output + pos, &got, in.data(), (uLong)in_size);
				if (rc != Z_OK) error("modern_replay: zlib 풀기 실패 (%d)", rc);
				pos += got;
			} else {
				size_t n = std::min(in_size, out_size);
				std::copy(in.begin(), in.begin() + n, output + pos);
				pos += n;
			}
		}
		if (getenv("BWDUMP_DUMPSEC") && output_size > 100000) {
			char nm[256]; snprintf(nm, sizeof nm, "%s.%zu", getenv("BWDUMP_DUMPSEC"), output_size);
			FILE* f = fopen(nm, "wb"); if (f) { fwrite(output, 1, pos, f); fclose(f);
				fprintf(stderr, "  [토막 저장] %s (%zu/%zu 바이트)\n", nm, pos, output_size); }
		}
		if (pos != output_size) error("modern_replay: %d 바이트 읽음, %d 기대", (int)pos, (int)output_size);
	}

	template<typename T, bool little_endian = default_little_endian>
	T get() {
		T v = get_impl<T, little_endian>(*this);
		/* 맨 처음 읽는 4바이트가 형식 표식이다. OpenBW의 load_replay는 옛 표식
		   "reRS"만 받으므로, 리마스터의 "seRS"를 그 자리에서 옛 표식으로 옮겨 준다 —
		   **뒤따르는 배치는 두 형식이 똑같다**(633바이트 게임 정보 → 토막들). 다른 것은
		   압축뿐이고 그건 위 get_bytes가 이미 흡수했다. OpenBW 원본을 안 고치려고 이
		   자리에서 옮긴다(그 파일은 clone으로 받아 오는 남의 소스다). */
		if (first && sizeof(T) == 4) {
			first = false;
			uint32_t* p = (uint32_t*)&v;
			if (*p == 0x53526573) *p = 0x53526572;
		}
		return v;
	}
	bool first = true;
	int framed_reads = 0;
};

template<typename base_reader_T>
auto make_modern_replay_file_reader(base_reader_T& reader) {
	return modern_replay_file_reader<base_reader_T>(reader);
}

/** 이 파일이 리마스터(1.21+) 형식인가 — 12번째 바이트가 's'면 그렇다(screp의 판별과 같다). */
inline bool is_modern_replay(const char* path) {
	FILE* f = fopen(path, "rb");
	if (!f) return false;
	unsigned char h[30] = {0};
	size_t n = fread(h, 1, sizeof h, f);
	fclose(f);
	return n >= 30 && h[12] == 's';
}

}
}
