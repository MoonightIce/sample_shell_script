#!/bin/bash

#echo "Shell 传递参数实例！"; echo 输出到打印台
# $ 代表着输入参数，$0 指执行的文件路径
#echo "执行的文件名：$0";
#echo "第一个参数为：$1";

fold_path=$1
if [ ! $2 ]; then
    target_fold=$1
else
    target_fold=$2
fi
echo "move files from $fold_path ---- $target_fold "

function moveFile() {
# $1 是文件所在的路径 $2 是文件名
if [ $1 != $target_fold ]; then
    if [ -f $target_fold"/"$2 ]; then
        mv $1"/"$2 $target_fold"/"$2"_1"
    else
        mv $1"/"$2 $target_fold
    fi
fi
}

function is_empty_dir(){
    return $(ls -A $1|wc -w)
}
# wc 语法 统计指定的字数或行数 wc是(Word Count)的缩写
#-c 统计字节数。
#-l 统计行数。
#-m 统计字符数。这个标志不能与 -c 标志一起使用。
#-w 统计字数。一个字被定义为由空白、跳格或换行字符分隔的字符串。
#-L 打印最长行的长度。
#-help 显示帮助信息
#--version 显示版本信息

function getAllDir() {
for file in $(ls $1)
do
    # 拼接路径 判断是文件还是文件夹
    dir_or_file=$1"/"$file
    if [ -d $dir_or_file ]; then
#        echo $dir_or_file
        getAllDir $dir_or_file
    elif [ -f "$dir_or_file" ];then
#        echo "$file"
        # 将文件移动到目标文件夹
        moveFile $1 $file
    else
        echo "$dir_or_file none"
    fi
done
# 删除还有问题，文件夹内会有隐藏文件，就没有删除
if is_empty_dir $1 ; then
    
#    echo "empty"
    rm -r $1
else
    echo $1
    empty=$(ls -A $1)
    echo " not empty"
fi
}
getAllDir $fold_path


# 获取当前列表下的文件 $(ls $fold_path)
#for file in $(ls $fold_path)
#do
#    # 拼接路径 判断是文件还是文件夹
#    dir_or_file=$fold_path"/"$file
#    if [ -d "$dir_or_file" ]; then
#        echo "$file is a dir"
#    elif [ -f "$dir_or_file" ];then
#        echo "$file is a file"
#    else
#        echo "it is a none"
#    fi
#done
