install.packages("dplyr") 
library(dplyr)


amostraIdade <- sample(sample(x = c(1:100), 
                              size = 1000, 
                              replace = TRUE, 
                              prob = c(0.005, 0.005, 0.005,0.005,0.005,0.005,0.005,0.005,0.005,0.005, 0.0075, 0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0075,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0125,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.0175,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.002,0.002,0.002,0.002,0.002,0.002,0.002,0.002,0.002,0.002)))

mean(amostraIdade)

amostraHorario <- sample(x = c(0:23),
                         size = 1000,
                         replace = TRUE,
                         prob = c(0.009,0.009,0.009,0.009,0.009,0.009,0.009,0.125,0.125,0.125,0.125,0.045,0.045,0.045,0.045,0.045,0.045,0.045,0.045,0.041,0.009,0.009,0.009,0.009))

amostraSexo <- sample(x = c(1, 2),
                      size = 1000,
                      replace = TRUE,
                      prob = c(0.4, 0.6))

set.seed(123)
amostraSalario <- abs(round(rnorm(1000, 6000, 2000),2))
round(rnorm(1000, 5000, 2000),2)

amostraFumantes <- sample(x = c("Não Fumante", "Fumante"),
                          size = 1000,
                          replace = TRUE,
                          prob = c(0.8, 0.2))

amostraMes <- sample(x = c("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"),
                     size = 1000,
                     replace = TRUE,
                     prob = c(0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05, 0.3,0.2,0.05))

amostraMes <- factor(
  amostraMes,
  levels = c("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")
)

dfUsuarios <- data.frame(
  Sexo = amostraSexo,
  Idade = amostraIdade,
  Salario = amostraSalario,
  Fumante = amostraFumantes,
  Horario = amostraHorario,
  Mês = amostraMes
)

hist(dfUsuarios$Idade,
     main = "Frequência de exames por idade",
     xlab = "Idade",
     ylab = "quantidade de exames")

hist(dfUsuarios$Horario,
     main = "Frequência de exames por horário",
     xlab = "Horário",
     ylab = "quantidade de exames")

hist(dfUsuarios$Salario,
     main = "Frequência de exames por salário",
     xlab = "Salário",
     ylab = "quantidade de exames")

barplot(table(dfUsuarios$Sexo),
        names= c("Masculino", "Feminino"),
        main = "Frequência de exames por sexo",
        ylab = "quantidade de exames")

barplot(table(dfUsuarios$Fumante),
        main = "Frequência de exames por fumante",
        ylab = "quantidade de exames")

set.seed(123)
amostraCPU <- ifelse(
  amostraHorario >= 0 & amostraHorario <= 6,
  abs(round(rnorm(length(amostraHorario), 10, 5),2)),
  
  ifelse(amostraHorario >= 7 & amostraHorario <= 10,
         abs(round(rnorm(length(amostraHorario), 70, 5),2)),
         
         ifelse(amostraHorario <= 19,
                abs(round(rnorm(length(amostraHorario), 30, 10),2)),
                abs(round(rnorm(length(amostraHorario), 10, 5),2))
         )
  )
)

set.seed(123)
amostraRAM <- ifelse(
  amostraHorario >= 0 & amostraHorario <= 6,
  abs(round(rnorm(length(amostraHorario), 20, 5),2)),
  
  ifelse(amostraHorario >= 7 & amostraHorario <= 10,
         abs(round(rnorm(length(amostraHorario), 75, 5),2)),
         
         ifelse(amostraHorario <= 19,
                abs(round(rnorm(length(amostraHorario), 40, 10),2)),
                abs(round(rnorm(length(amostraHorario), 20, 5),2))
         )
  )
)

set.seed(123)
amostraDisco <- ifelse(
  amostraHorario >= 0 & amostraHorario <= 6,
  abs(round(rnorm(length(amostraHorario), 5, 1),2)),
  
  ifelse(amostraHorario >= 7 & amostraHorario <= 10,
         abs(round(rnorm(length(amostraHorario), 60, 5),2)),
         
         ifelse(amostraHorario <= 19,
                abs(round(rnorm(length(amostraHorario), 30, 15),2)),
                abs(round(rnorm(length(amostraHorario), 5, 1),2))
         )
  )
)

amostraCPUMes <- case_when(
  amostraMes == "Janeiro"   ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Fevereiro" ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Março"     ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Abril"     ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Maio"      ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Junho"     ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Julho"     ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Agosto"    ~ abs(round(rnorm(length(amostraMes), 30, 5), 2)),
  amostraMes == "Setembro"  ~ abs(round(rnorm(length(amostraMes), 30, 8), 2)),
  amostraMes == "Outubro"   ~ abs(round(rnorm(length(amostraMes), 75, 10), 2)),
  amostraMes == "Novembro"  ~ abs(round(rnorm(length(amostraMes), 70, 10), 2)),
  amostraMes == "Dezembro"  ~ abs(round(rnorm(length(amostraMes), 30, 8), 2))
)
 
hist(amostraCPU,
     main = "Frequência de uso da CPU",
     xlab = "Uso da CPU em %",
     ylab = "Frequência")

hist(amostraRAM,
     main = "Frequência de uso da RAM",
     xlab = "Uso da RAM em %",
     ylab = "Frequência")

hist(amostraDisco,
     main = "Frequência de uso de Disco",
     xlab = "Uso de Disco em %",
     ylab = "Frequência")


media_por_hora_CPU <- aggregate(amostraCPU ~ amostraHorario, FUN = mean)

media_por_hora_RAM <- aggregate(amostraRAM ~ amostraHorario, FUN = mean)

media_por_hora_Disco <- aggregate(amostraDisco ~ amostraHorario, FUN = mean)

media_por_mes_CPU <- aggregate(amostraCPUMes ~ amostraMes, FUN = mean)

barplot(
  media_por_hora_CPU$amostraCPU,
  names.arg = media_por_hora_CPU$amostraHorario,
  xlab = "Hora do dia",
  ylab = "Média uso CPU (%)",
  main = "Uso médio da CPU por horário"
)

barplot(
  media_por_hora_RAM$amostraRAM,
  names.arg = media_por_hora_RAM$amostraHorario,
  xlab = "Hora do dia",
  ylab = "Média uso RAM (%)",
  main = "Uso médio da RAM por horário"
)

barplot(
  media_por_hora_Disco$amostraDisco,
  names.arg = media_por_hora_Disco$amostraHorario,
  xlab = "Hora do dia",
  ylab = "Média uso Disco (%)",
  main = "Uso médio de Disco por horário"
)

barplot(
  media_por_mes_CPU$amostraCPU,
  names.arg = media_por_mes_CPU$amostraMes,
  xlab = "Mês",
  ylab = "Média uso CPU (%)",
  main = "Uso médio de CPU por mês"
)

boxplot(amostraCPU, amostraRAM, amostraDisco,
        names = c("CPU","RAM","Disco"),
        main = "Boxplot de uso de CPU RAM e Disco em %",
        ylab = "Uso em %")



