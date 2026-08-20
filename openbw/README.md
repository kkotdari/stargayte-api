# 참값 트랙 굽기 — OpenBW 헤드리스 덤퍼

리플레이를 **실제로 시뮬레이션해서** 유닛의 참 자리·방향·상태를 뽑는다. 여태 프론트가
커맨드에서 유추해 만들던 트랙을 대체한다. 원장은 프론트 리포의 `tools/openbw/README.md`다 —
왜 이렇게 만들었는지, 무엇을 고쳤고 무엇을 배제했는지가 전부 거기 있다.

## 여기 있는 것

| 파일 | 하는 일 |
|---|---|
| `bwdump.cpp` | 덤퍼 본체. `--tracks --bin`으로 조밀 이진 트랙을 낸다 |
| `modern_replay.h` | 리마스터(1.21+) 리플레이 읽개 — `seRS`·zlib |
| `openbw-scr.patch` | OpenBW 원본에 얹는 우리 고침(리마스터 태그·그릇 한도·팀전 동맹 따위) |

OpenBW 원본은 **여기 안 둔다**(라이선스 표기가 없다). Dockerfile이 빌드할 때 아래 커밋으로
받아서 패치를 얹는다.

    OpenBW 커밋: 4b046d5f65302b10cb0a745f0fecd37ec85b20a8

## 게임 자료는 볼륨에

덤퍼는 스타크래프트의 자료 파일 열 몇 개(`arr/*.dat`, `arr/images.tbl`, `scripts/iscript.bin`,
`Tileset/*`)가 있어야 돈다. 그림·소리는 한 장도 안 쓴다.

이 자료는 **리포에도 이미지에도 안 넣는다.** 볼륨 안(`var/bwdata` — `OPENBW_DATA_ROOT`로
바꿀 수 있다)에 한 번만 올려 두면 된다. 뽑는 법은 프론트 리포의 `tools/openbw/cascextract`를
봐라(스타크래프트 설치 폴더에서 뽑는다). 955개 16MB이고 묶으면 6.9MB다.

## 올리는 법 — 문을 열고, 올리고, 도로 닫는다

Railway 볼륨은 밖에서 파일을 밀어 넣을 길이 없다. 그래서 **일회용 문**을 하나 냈다.

1. `OPENBW_BOOTSTRAP_TOKEN`에 아무 문자열이나 넣고 배포한다(영문·숫자로 — HTTP 헤더로
   보내는 값이라 한글은 아예 못 실린다). 이 값이 비어 있으면 문은 **없는 길**이다(404).
2. 자료를 묶어 올린다. 맥에서는 `COPYFILE_DISABLE=1`을 꼭 붙여라 — 안 붙이면 파일마다
   `._이름`(AppleDouble)이 딸려 와 개수가 두 배가 된다(받는 쪽에서 걸러 내긴 한다).

   ```
   COPYFILE_DISABLE=1 tar czf bwdata.tgz -C <자료폴더> .
   curl -X POST https://<서버>/api/openbw/data \
        -H "X-Bootstrap-Token: <토큰>" \
        -H "Content-Type: application/gzip" \
        --data-binary @bwdata.tgz
   ```

   `{"files":955,"bytes":14146383,"ready":true,"reason":""}`가 오면 된 것이다.
   `ready`가 false면 `reason`이 왜 아직 못 굽는지 말해 준다.
3. **`OPENBW_BOOTSTRAP_TOKEN`을 비우고 다시 배포한다.** 문이 닫힌다.

받는 쪽이 지키는 선(`openbw_bootstrap.py`): 볼륨 밖을 가리키는 이름(`..`·절대경로)이나
심볼릭 링크가 **하나라도** 있으면 한 파일도 안 쓰고 통째로 거절한다. 갈래는 덤퍼가 실제로
읽는 다섯(`arr`·`scripts`·`triggers`·`Tileset`·`unit`)만 받고 나머지는 버린다. 그리고
이 파일들을 **돌려주는 길은 없다** — 문은 쓰기 전용이다.

자료나 바이너리가 없으면 **굽기만 조용히 건너뛴다** — 앱 기동에는 영향이 없다. 다만 프론트에
폴백이 없으므로, 자료를 안 올린 서버에서는 모든 경기가 "재생할 수 없는 게임"으로 보인다.
