# sample_shell_script
简单的脚本
### MoveFiles
可以接受两个参数，第一个参数是需要移动文件的目录 fold_path 。第二参数是移动文件的目标目录 target_fold ，如果不传就在移动到当前的目录 fold_path。
将文件移动之后，会判断文件夹是否为空，为空就会删除文件夹。当前的判断为空忽略了隐藏文件。如果不忽略，就用 `is_empty_dir()` 的判断方法
