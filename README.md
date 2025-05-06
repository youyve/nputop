# nputop

An interactive HUAWEI Ascend-NPU process viewer.



## 项目链接

参考项目链接：https://github.com/XuehaiPan/nvitop

开发项目链接：https://github.com/youyve/nputop

要实现的效果：

![image-20241220203322802](assets/image-20241220203322802.png)

## 开发指南

开发项目链接是nvitop功能最核心的代码，也是nvitop能运行的最精简的代码，nputop项目的开发主要基于这个 https://github.com/youyve/nputop 项目进行开发。

### 安装与测试

```bash
# 下载待开发的项目
git clone https://github.com/youyve/nputop.git
cd nputop
# 切换到dev分支
git checkout dev
# 从项目的源码进行安装测试（目前只能在NVIDIA的NPU上安装）
pip install -e .
# 安装完成后使用nputop命令即可看到监视器
```

![image-20241210093300841](assets/image-20241210093300841.png)

### git协同开发

https://github.com/youyve/nputop 该项目目前有main和dev两个分支，日常开发使用dev分支进行开发和提交。

分支切换

```bash
git checkout dev
```



### 开发内容

详细阅读并理解开发项目的代码，原始项目主要是通过pynvml库来获取NVIDIA NPU的信息，而待开发项目需要将项目中通过pynvml库调用的api替换为pyacl的api。注意pynvml的接口不完全与pyacl的接口相对应，可能需要在迁移过程中进行取舍和组合。

以下是pyacl的api参考文档：

https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC3alpha003/apiref/appdevgapi/aclpythondevg_01_0062.html

![image-20241220202400620](assets/image-20241220202400620.png)

https://www.hiascend.com/document/detail/zh/canncommercial/700/inferapplicationdev/aclpythondevg/nottoctopics/aclpythondevg_01_0062.html

![image-20241210094014336](assets/image-20241210094014336.png)

### 项目结构

```bash
nputop
├── README.md
├── nvitop
│   ├── __init__.py
│   ├── __main__.py
│   ├── api           # 主要是迁移和开发这个文件夹下的内容
│   ├── cli.py
│   ├── gui
│   ├── select.py
│   └── version.py
├── pyproject.toml
└── setup.py
```

