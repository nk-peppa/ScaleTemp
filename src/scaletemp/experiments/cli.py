from __future__ import annotations

from scaletemp.processing.service import ScaleService
from scaletemp.experiments.workflows import ExperimentRunner


def wait(prompt: str) -> None:
    input(f"\n{prompt}\nPress ENTER / 按回车继续...")


def main() -> None:
    service = ScaleService()
    service.start()
    runner = ExperimentRunner(service)
    try:
        while True:
            print("""
------------------------------------------------
[1] Calibration / 校准
[2] Filtering Test / 滤波测试
[3] Dynamic Response Test / 动态响应测试
[4] Repeatability Test / 重复性测试
[5] Creep/Drift Test / 蠕变漂移测试
[6] Auto-zero Test / 自动回零测试
[7] Generate Final Report Figures / 查看已生成图像
[0] Exit / 退出
------------------------------------------------
""")
            choice = input("Select workflow: ").strip()
            if choice == "0":
                break
            if choice == "1":
                masses = input("Calibration masses in grams (comma separated, default 0,100,200,500,1000): ").strip()
                mass_list = [float(x) for x in (masses or "0,100,200,500,1000").split(",")]
                wait("Remove all weight / 移除所有重量")
                result = runner.calibration(mass_list)
            elif choice == "2":
                wait("Place a stable load and do not touch the system / 放置稳定载荷且不要触碰")
                result = runner.filtering()
            elif choice == "3":
                wait("Prepare to place 500g load quickly / 准备快速放置 500g 载荷")
                print("3...2...1...")
                result = runner.dynamic()
                wait("Remove the load quickly / 快速移除载荷")
            elif choice == "4":
                trials = int(input("Trials (default 5): ") or "5")
                wait("Repeatedly place the same load when prompted / 每次按提示放置相同载荷")
                result = runner.repeatability(trials=trials)
            elif choice == "5":
                minutes = float(input("Duration minutes (default 10): ") or "10")
                wait("Place a constant load and leave untouched / 放置恒定载荷并保持不动")
                result = runner.drift(duration_s=minutes * 60)
            elif choice == "6":
                wait("Remove all weight now / 立即移除所有重量")
                result = runner.auto_zero()
            elif choice == "7":
                print("Figures are under data/figures. Dashboard download links are also available.")
                continue
            else:
                print("Unknown choice")
                continue
            print(f"Completed: {result.name}")
            print(f"Raw: {result.raw_csv}")
            print(f"Processed: {result.processed_csv}")
            for fig in result.figures:
                print(fig)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
