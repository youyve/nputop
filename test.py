from nputop.api import libascend as libnvml
from nputop import NA  # 导入 NA 常量
import sys

def test_libascend():
    # 测试初始化
    print("Testing ascendInit...")
    libnvml.ascendInit()  # 线程安全初始化
    try:
        # 测试设备计数
        count = libnvml.nvmlQuery("ascendDeviceGetCount")
        print(f"Number of Ascend devices: {count}")

        if count == 0:
            print("No Ascend devices found. Exiting.")
            return

        # 测试每个设备的信息
        for i in range(count):
            print(f"\n=== NPU Device {i} ===")
            
            # 设备名称
            name = libnvml.nvmlQuery("ascendDeviceGetName", i)
            print(f"Device Name: {name if name != NA else 'N/A'}")

            # 内存信息
            mem = libnvml.nvmlQuery("ascendDeviceGetMemoryInfo", i)
            if mem != NA:
                print(f"Memory: Total {mem.total/2**20:.0f} MiB, "
                      f"Used {mem.used/2**20:.0f} MiB, "
                      f"Free {mem.free/2**20:.0f} MiB")
            else:
                print("Memory Info: N/A")

            # 利用率
            util = libnvml.nvmlQuery("ascendDeviceGetUtilizationRates", i)
            if util != NA:
                print(f"Utilization: AI Core {util.ai_core}%, "
                      f"Memory {util.mem}%, "
                      f"Bandwidth {util.bandwidth}%, "
                      f"AICPU {util.aicpu}%")
            else:
                print("Utilization Rates: N/A")

            # 温度
            temp = libnvml.nvmlQuery("ascendDeviceGetTemperature", i)
            print(f"Temperature: {temp}°C" if temp != NA else "Temperature: N/A")

            # 功耗
            power = libnvml.nvmlQuery("ascendDeviceGetPowerUsage", i)
            print(f"Power Usage: {power/1000:.1f} W" if power != NA else "Power Usage: N/A")

            # 进程信息
            procs = libnvml.nvmlQuery("ascendDeviceGetProcessInfo", i)
            if procs:
                print("Running Processes:")
                for proc in procs:
                    print(f"  PID {proc.pid}: {proc.usedNpuMemory/2**20:.0f} MiB")
            else:
                print("Running Processes: None or N/A")

    finally:
        # 清理
        print("\nCleaning up...")
        libnvml.ascendShutdown()

if __name__ == "__main__":
    # 使用上下文管理器测试模块
    with libnvml:
        test_libascend()